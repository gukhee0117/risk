"""Disposable-container-only migration drill against the repository's full legacy DDL.

No production connection parameters are accepted. No customer seed/operating
data is read: only public schema and locally generated synthetic risk rows.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
MARIA = ROOT.parent / "maria"
CONTAINER = "maria-risk-http-20261029"
SCHEMA = "risk_shape_test"


def command(args, *, source=None, check=True):
    return subprocess.run(args, input=source, capture_output=True, check=check, timeout=60)


def sql(text, *, check=True):
    result = command(["docker", "exec", "-i", CONTAINER, "mariadb", "--user=root",
                      "--password=risk-test-only", "--default-character-set=utf8mb4",
                      "--batch", "--skip-column-names"], source=text.encode(), check=check)
    return result.stdout.decode("utf-8"), result


def query(text):
    return sql(f"USE {SCHEMA};\n{text}")[0].strip()


def snapshot():
    statements = {
        "products": "SELECT domestic_product_id,ticker,name,market,type,domestic_stock_ratio,inception_date FROM domestic_product ORDER BY domestic_product_id;",
        "grades": "SELECT domestic_product_id,var_975,risk_grade,volatility,price_from,price_to,calculated_at,synced_at FROM stock_risk_grade ORDER BY domestic_product_id;",
        "business_clock": "SELECT clock_id,current_datetime,reference_real_datetime FROM system_clock;",
    }
    return {name: hashlib.sha256(query(statement).encode()).hexdigest()
            for name, statement in statements.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--container-id", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--legacy-ref", default="96060a2ae5d62b556082fea3f708302dc63de42f",
                        help="Reviewed pre-migration MARIA commit (full SHA)")
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{40}", args.legacy_ref):
        raise RuntimeError("An explicit reviewed commit SHA is required")
    actual = command(["docker", "inspect", "--format", "{{.Id}}", CONTAINER]).stdout.decode().strip()
    if actual != args.container_id or not re.fullmatch("[0-9a-f]{64}", actual):
        raise RuntimeError("Test container ownership check failed")
    label = command(["docker", "inspect", "--format", '{{index .Config.Labels "task"}}', CONTAINER]).stdout.decode().strip()
    if label != "maria-risk-http-verification":
        raise RuntimeError("Dedicated test label missing")

    raw = command(["git", "-C", str(MARIA), "show", f"{args.legacy_ref}:db/01_ria_ddl.sql"]).stdout
    legacy = raw.decode("utf-8-sig")
    if "analysis_version" in legacy:
        raise RuntimeError("The reviewed commit is not a legacy schema")
    if legacy.count("CREATE DATABASE IF NOT EXISTS ria_admin") != 1 or legacy.count("USE ria_admin;") != 1:
        raise RuntimeError("Unexpected legacy schema selection")
    legacy = legacy.replace("CREATE DATABASE IF NOT EXISTS ria_admin", f"CREATE DATABASE IF NOT EXISTS {SCHEMA}")
    legacy = legacy.replace("USE ria_admin;", f"USE {SCHEMA};")
    if re.search(r"\b(?:USE|CREATE DATABASE IF NOT EXISTS)\s+ria_admin\b", legacy, re.I):
        raise RuntimeError("Unsafe schema selection")
    migration = (MARIA / "db/migrations/001_stock_risk_grade_version.sql").read_text(encoding="utf-8-sig")
    match = re.search(r"ALTER TABLE stock_risk_grade.*?;", migration, re.S)
    if match is None:
        raise RuntimeError("Migration ALTER not found")
    alter = match.group(0)
    rows = list(csv.DictReader((ROOT / "config/universe.csv").open(encoding="utf-8")))
    assert len(rows) == 77

    def reset():
        # This name is fixed and exists only inside the owned disposable container.
        sql(f"DROP DATABASE IF EXISTS {SCHEMA};\n" + legacy)
        products, grades = [], []
        for index, row in enumerate(rows, 1):
            ticker = row["ticker"]
            assert re.fullmatch(r"\d{6}", ticker)
            products.append(f"({index},'{ticker}','Synthetic product {index}','TEST','STOCK')")
            grades.append(f"({index},0.12,4,0.23,'2023-10-30','2026-10-28','2026-10-28 15:41:01','2026-10-28 17:10:00')")
        sql(f"USE {SCHEMA};\nINSERT INTO domestic_product(domestic_product_id,ticker,name,market,type) VALUES "
            + ",".join(products) + ";\nINSERT INTO stock_risk_grade VALUES " + ",".join(grades)
            + ";\nINSERT INTO system_clock(clock_id,current_datetime) VALUES (1,'2026-08-17 09:00:00');")
        return snapshot()

    report = {"container": CONTAINER, "schema": SCHEMA, "synthetic": True,
              "legacy_commit": args.legacy_ref,
              "legacy_ddl_sha256": hashlib.sha256(raw).hexdigest(), "cases": []}
    before = reset()
    count = int(query("SELECT count(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE();"))
    assert count > 30
    sql(f"USE {SCHEMA};\n" + migration)
    assert snapshot() == before
    assert query("SELECT count(*) FROM stock_risk_grade WHERE analysis_version=0;") == "77"
    sql(f"USE {SCHEMA};\n" + migration)
    assert snapshot() == before
    report["table_count_before"] = count
    report["table_count_after"] = int(query("SELECT count(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE();"))
    report["cases"].append({"name": "full_structure_migration_and_rerun", "passed": True,
                            "preserved_risk_rows": 77, "preserved_product_rows": 77, "checksums": before})

    before = reset()
    # Simulate a process stopping after successful ALTER, before state-table DDL.
    sql(f"USE {SCHEMA};\n" + alter)
    assert query("SELECT count(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='risk_grade_sync_state';") == "0"
    assert snapshot() == before
    sql(f"USE {SCHEMA};\n" + migration)
    assert snapshot() == before
    assert query("SELECT count(*) FROM risk_grade_sync_state WHERE state_id=1;") == "1"
    report["cases"].append({"name": "interruption_after_alter_then_resume", "passed": True})

    before = reset()
    _, failure = sql(f"USE {SCHEMA};\nALTER TABLE stock_risk_grade ADD COLUMN analysis_version BIGINT NOT NULL DEFAULT 0,"
                     " ADD CONSTRAINT deliberately_invalid_test CHECK(risk_grade<0);", check=False)
    assert failure.returncode != 0
    assert snapshot() == before
    assert query("SELECT count(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='stock_risk_grade' AND COLUMN_NAME='analysis_version';") == "0"
    sql(f"USE {SCHEMA};\n" + migration)
    assert snapshot() == before
    report["cases"].append({"name": "failed_alter_preserves_rows_and_schema_then_resume", "passed": True})
    report["ddl_whole_transaction_rollback_supported"] = False
    report["runtime"] = query("SELECT VERSION(),@@session.time_zone,@@global.time_zone;")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(report["cases"]), "passed": True,
                      "legacy_tables": count, "risk_rows": 77, "whole_ddl_rollback": False}))


if __name__ == "__main__":
    main()
