# Databricks notebook source
# MAGIC %md
# MAGIC # ResearchMatch — ingest ZU Scholars OAI-PMH into Bronze
# MAGIC
# MAGIC Run `sql/01_create_bronze_tables.sql` first. This notebook:
# MAGIC
# MAGIC - harvests the `publication:works` set in `oai_dc` format;
# MAGIC - follows opaque OAI-PMH resumption tokens;
# MAGIC - stores one serialized `<record>` element per Delta row;
# MAGIC - keeps every distinct source version using a SHA-256 logical key;
# MAGIC - records run metrics and request/record errors;
# MAGIC - does not download publication files.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "01 Target catalog")
dbutils.widgets.text("bronze_schema", "researchmatch_bronze", "02 Bronze schema")
dbutils.widgets.text(
    "base_url",
    "https://zuscholars.zu.ac.ae/do/oai/",
    "03 OAI-PMH base URL",
)
dbutils.widgets.text("metadata_prefix", "oai_dc", "04 Metadata prefix")
dbutils.widgets.text("set_spec", "publication:works", "05 OAI set")
dbutils.widgets.text("harvest_from", "", "06 From UTC (optional)")
dbutils.widgets.text("harvest_until", "", "07 Until UTC (optional)")
dbutils.widgets.text("max_pages", "0", "08 Max pages; 0 = unlimited")
dbutils.widgets.text("write_batch_size", "1000", "09 Records per Delta write")
dbutils.widgets.text("request_timeout_seconds", "60", "10 Request timeout")
dbutils.widgets.text("request_delay_seconds", "0.25", "11 Delay between pages")

# COMMAND ----------

import hashlib
import json
import re
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from pyspark.sql import functions as F
from pyspark.sql.types import (
    ArrayType,
    BooleanType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)


OAI_NS = "http://www.openarchives.org/OAI/2.0/"
NS = {"oai": OAI_NS}
ET.register_namespace("", OAI_NS)


def widget_text(name: str) -> str:
    return dbutils.widgets.get(name).strip()


def validate_identifier(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def utc_now_naive() -> datetime:
    """Return UTC without tzinfo, matching Spark TIMESTAMP semantics."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_oai_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


catalog = validate_identifier(widget_text("catalog"), "catalog")
bronze_schema = validate_identifier(widget_text("bronze_schema"), "schema")
base_url = widget_text("base_url")
metadata_prefix = widget_text("metadata_prefix")
set_spec = widget_text("set_spec")
harvest_from_text = widget_text("harvest_from")
harvest_until_text = widget_text("harvest_until")
max_pages = int(widget_text("max_pages"))
write_batch_size = int(widget_text("write_batch_size"))
request_timeout_seconds = int(widget_text("request_timeout_seconds"))
request_delay_seconds = float(widget_text("request_delay_seconds"))

if not base_url.lower().startswith("https://"):
    raise ValueError("base_url must use HTTPS")
if max_pages < 0:
    raise ValueError("max_pages must be zero or positive")
if write_batch_size < 1:
    raise ValueError("write_batch_size must be at least 1")
if request_timeout_seconds < 1:
    raise ValueError("request_timeout_seconds must be at least 1")
if request_delay_seconds < 0:
    raise ValueError("request_delay_seconds cannot be negative")

harvest_from = parse_oai_timestamp(harvest_from_text)
harvest_until = parse_oai_timestamp(harvest_until_text)
if harvest_from and harvest_until and harvest_from > harvest_until:
    raise ValueError("harvest_from cannot be later than harvest_until")

table_prefix = f"{catalog}.{bronze_schema}"
runs_table = f"{table_prefix}.ingestion_runs"
errors_table = f"{table_prefix}.ingestion_errors"
records_table = f"{table_prefix}.zu_scholars_oai_raw"

required_tables = [runs_table, errors_table, records_table]
missing_tables = [name for name in required_tables if not spark.catalog.tableExists(name)]
if missing_tables:
    raise RuntimeError(
        "Missing required Delta tables. Run sql/01_create_bronze_tables.sql first: "
        + ", ".join(missing_tables)
    )

# COMMAND ----------

run_schema = StructType(
    [
        StructField("ingestion_run_id", StringType(), False),
        StructField("source_system", StringType(), False),
        StructField("dataset_name", StringType(), False),
        StructField("started_at", TimestampType(), False),
        StructField("completed_at", TimestampType(), True),
        StructField("status", StringType(), False),
        StructField("request_parameters_sanitized", StringType(), True),
        StructField("watermark_or_cursor_start", StringType(), True),
        StructField("watermark_or_cursor_end", StringType(), True),
        StructField("pages_requested", LongType(), False),
        StructField("records_received", LongType(), False),
        StructField("records_inserted", LongType(), False),
        StructField("records_updated", LongType(), False),
        StructField("records_unchanged", LongType(), False),
        StructField("records_failed", LongType(), False),
        StructField("http_status_summary", StringType(), True),
        StructField("error_message", StringType(), True),
        StructField("created_by", StringType(), True),
    ]
)

error_schema = StructType(
    [
        StructField("error_id", StringType(), False),
        StructField("ingestion_run_id", StringType(), False),
        StructField("source_system", StringType(), False),
        StructField("dataset_name", StringType(), False),
        StructField("source_record_id", StringType(), True),
        StructField("request_context_sanitized", StringType(), True),
        StructField("error_type", StringType(), False),
        StructField("error_message", StringType(), False),
        StructField("raw_error_payload", StringType(), True),
        StructField("occurred_at", TimestampType(), False),
    ]
)

record_schema = StructType(
    [
        StructField("record_key", StringType(), False),
        StructField("oai_identifier", StringType(), False),
        StructField("oai_datestamp", TimestampType(), True),
        StructField("set_specs", ArrayType(StringType()), True),
        StructField("is_deleted", BooleanType(), False),
        StructField("metadata_prefix", StringType(), False),
        StructField("response_date", TimestampType(), True),
        StructField("resumption_token_used", StringType(), True),
        StructField("harvest_from", TimestampType(), True),
        StructField("harvest_until", TimestampType(), True),
        StructField("request_url", StringType(), False),
        StructField("record_hash", StringType(), False),
        StructField("raw_record_xml", StringType(), False),
        StructField("fetched_at", TimestampType(), False),
        StructField("ingestion_run_id", StringType(), False),
    ]
)

source_system = "zu_scholars"
dataset_name = "oai_works"
run_id = str(uuid.uuid4())
started_at = utc_now_naive()
created_by = spark.sql("SELECT current_user() AS user").first()["user"]

request_parameters = {
    "base_url": base_url,
    "verb": "ListRecords",
    "metadata_prefix": metadata_prefix,
    "set_spec": set_spec or None,
    "harvest_from": harvest_from_text or None,
    "harvest_until": harvest_until_text or None,
    "max_pages": max_pages,
}

start_row = (
    run_id,
    source_system,
    dataset_name,
    started_at,
    None,
    "started",
    json.dumps(request_parameters, sort_keys=True),
    harvest_from_text or None,
    None,
    0,
    0,
    0,
    0,
    0,
    0,
    json.dumps({}),
    None,
    created_by,
)

spark.createDataFrame([start_row], run_schema).write.mode("append").saveAsTable(runs_table)
print(f"Started ZU Scholars harvest: {run_id}")

# COMMAND ----------

pages_requested = 0
records_received = 0
records_inserted = 0
records_updated = 0
records_unchanged = 0
records_failed = 0
http_status_counts: Counter[str] = Counter()


def write_error(
    error_type: str,
    error_message: str,
    *,
    source_record_id: Optional[str] = None,
    request_context: Optional[dict[str, Any]] = None,
    raw_error_payload: Optional[str] = None,
) -> None:
    row = (
        str(uuid.uuid4()),
        run_id,
        source_system,
        dataset_name,
        source_record_id,
        json.dumps(request_context, sort_keys=True) if request_context else None,
        error_type,
        error_message[:10000],
        raw_error_payload,
        utc_now_naive(),
    )
    spark.createDataFrame([row], error_schema).write.mode("append").saveAsTable(
        errors_table
    )


def update_run(status: str, cursor_end: Optional[str], error_message: Optional[str]) -> None:
    from delta.tables import DeltaTable

    DeltaTable.forName(spark, runs_table).update(
        condition=F.col("ingestion_run_id") == F.lit(run_id),
        set={
            "completed_at": F.lit(utc_now_naive()),
            "status": F.lit(status),
            "watermark_or_cursor_end": F.lit(cursor_end),
            "pages_requested": F.lit(pages_requested),
            "records_received": F.lit(records_received),
            "records_inserted": F.lit(records_inserted),
            "records_updated": F.lit(records_updated),
            "records_unchanged": F.lit(records_unchanged),
            "records_failed": F.lit(records_failed),
            "http_status_summary": F.lit(
                json.dumps(dict(sorted(http_status_counts.items())))
            ),
            "error_message": F.lit(error_message[:10000] if error_message else None),
        },
    )


def request_page(
    session: requests.Session,
    params: dict[str, str],
    *,
    max_attempts: int = 5,
) -> requests.Response:
    last_exception: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = session.get(
                base_url,
                params=params,
                timeout=request_timeout_seconds,
            )
            http_status_counts[str(response.status_code)] += 1

            if response.status_code == 429 or response.status_code >= 500:
                if attempt == max_attempts:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After", "")
                wait_seconds = min(
                    float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2 ** attempt,
                    60.0,
                )
                time.sleep(wait_seconds)
                continue

            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exception = exc
            if attempt == max_attempts:
                raise
            time.sleep(min(2 ** attempt, 60))

    raise RuntimeError("OAI-PMH request failed") from last_exception


def parse_page(
    response: requests.Response,
    token_used: Optional[str],
) -> tuple[list[dict[str, Any]], Optional[str]]:
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise RuntimeError("ZU Scholars returned invalid XML") from exc

    protocol_error = root.find("oai:error", NS)
    if protocol_error is not None:
        code = protocol_error.attrib.get("code", "unknown")
        message = (protocol_error.text or "").strip()
        if code == "noRecordsMatch":
            return [], None
        raise RuntimeError(f"OAI-PMH error {code}: {message}")

    list_records = root.find("oai:ListRecords", NS)
    if list_records is None:
        raise RuntimeError("OAI-PMH response did not contain ListRecords")

    response_date = parse_oai_timestamp(root.findtext("oai:responseDate", default=None, namespaces=NS))
    fetched_at = utc_now_naive()
    parsed_records: list[dict[str, Any]] = []

    for record in list_records.findall("oai:record", NS):
        source_record_id: Optional[str] = None
        try:
            header = record.find("oai:header", NS)
            if header is None:
                raise ValueError("Record has no OAI header")

            source_record_id = header.findtext(
                "oai:identifier", default=None, namespaces=NS
            )
            if not source_record_id:
                raise ValueError("Record has no OAI identifier")

            datestamp_text = header.findtext(
                "oai:datestamp", default=None, namespaces=NS
            )
            set_specs = [
                element.text.strip()
                for element in header.findall("oai:setSpec", NS)
                if element.text and element.text.strip()
            ]
            is_deleted = header.attrib.get("status") == "deleted"
            raw_record_xml = ET.tostring(record, encoding="unicode")
            record_hash = sha256_text(raw_record_xml)
            record_key = sha256_text(
                "|".join(
                    [
                        source_record_id,
                        datestamp_text or "",
                        metadata_prefix,
                        record_hash,
                    ]
                )
            )

            parsed_records.append(
                {
                    "record_key": record_key,
                    "oai_identifier": source_record_id,
                    "oai_datestamp": parse_oai_timestamp(datestamp_text),
                    "set_specs": set_specs,
                    "is_deleted": is_deleted,
                    "metadata_prefix": metadata_prefix,
                    "response_date": response_date,
                    "resumption_token_used": token_used,
                    "harvest_from": harvest_from,
                    "harvest_until": harvest_until,
                    "request_url": response.url,
                    "record_hash": record_hash,
                    "raw_record_xml": raw_record_xml,
                    "fetched_at": fetched_at,
                    "ingestion_run_id": run_id,
                }
            )
        except Exception as exc:
            global records_failed
            records_failed += 1
            write_error(
                "record_parse_error",
                str(exc),
                source_record_id=source_record_id,
                request_context={"request_url": response.url},
                raw_error_payload=ET.tostring(record, encoding="unicode"),
            )

    token_element = list_records.find("oai:resumptionToken", NS)
    next_token = None
    if token_element is not None and token_element.text:
        next_token = token_element.text.strip() or None

    return parsed_records, next_token


def append_new_versions(rows: list[dict[str, Any]]) -> None:
    global records_inserted, records_unchanged

    if not rows:
        return

    batch_df = spark.createDataFrame(rows, schema=record_schema).dropDuplicates(
        ["record_key"]
    )
    existing_keys = spark.table(records_table).select("record_key")
    new_versions = batch_df.join(existing_keys, on="record_key", how="left_anti").cache()
    inserted_in_batch = new_versions.count()

    if inserted_in_batch:
        new_versions.write.mode("append").saveAsTable(records_table)

    records_inserted += inserted_in_batch
    records_unchanged += len(rows) - inserted_in_batch
    new_versions.unpersist()

# COMMAND ----------

session = requests.Session()
session.headers.update(
    {
        "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.1",
        "User-Agent": "ResearchMatchAI-ZU-Scholars-Harvester/1.0",
    }
)

resumption_token: Optional[str] = None
pending_rows: list[dict[str, Any]] = []
run_status = "succeeded"

try:
    while True:
        if max_pages and pages_requested >= max_pages:
            if resumption_token:
                run_status = "partial"
            break

        if resumption_token:
            request_params = {
                "verb": "ListRecords",
                "resumptionToken": resumption_token,
            }
        else:
            request_params = {
                "verb": "ListRecords",
                "metadataPrefix": metadata_prefix,
            }
            if set_spec:
                request_params["set"] = set_spec
            if harvest_from_text:
                request_params["from"] = harvest_from_text
            if harvest_until_text:
                request_params["until"] = harvest_until_text

        token_used = resumption_token
        pages_requested += 1
        response = request_page(session, request_params)
        page_rows, resumption_token = parse_page(response, token_used)
        records_received += len(page_rows)
        pending_rows.extend(page_rows)

        if len(pending_rows) >= write_batch_size:
            append_new_versions(pending_rows)
            pending_rows.clear()

        print(
            f"page={pages_requested} received={len(page_rows)} "
            f"total_received={records_received} has_next={bool(resumption_token)}"
        )

        if not resumption_token:
            break
        if request_delay_seconds:
            time.sleep(request_delay_seconds)

    append_new_versions(pending_rows)
    pending_rows.clear()
    update_run(run_status, resumption_token, None)
except Exception as exc:
    # Preserve records from already successful pages even if a later page fails.
    if pending_rows:
        try:
            append_new_versions(pending_rows)
            pending_rows.clear()
        except Exception as flush_exc:
            records_failed += len(pending_rows)
            write_error(
                "final_batch_write_error",
                str(flush_exc),
                request_context={"pending_record_count": len(pending_rows)},
            )
    write_error(
        "harvest_error",
        str(exc),
        request_context={
            "base_url": base_url,
            "page": pages_requested,
            "has_resumption_token": bool(resumption_token),
        },
    )
    update_run("failed", resumption_token, str(exc))
    raise
finally:
    session.close()

summary = spark.table(runs_table).where(F.col("ingestion_run_id") == run_id)
display(summary)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Post-run checks
# MAGIC
# MAGIC The initial run should normally use blank `harvest_from` and `harvest_until` values.
# MAGIC Because the repository declares `deletedRecord=no`, schedule periodic full reconciliation
# MAGIC even if date-bounded refreshes are also used.

# COMMAND ----------

display(
    spark.table(records_table)
    .where(F.col("ingestion_run_id") == run_id)
    .select(
        "oai_identifier",
        "oai_datestamp",
        "set_specs",
        "is_deleted",
        "record_hash",
        "fetched_at",
    )
    .orderBy(F.col("oai_datestamp").desc_nulls_last())
    .limit(20)
)
