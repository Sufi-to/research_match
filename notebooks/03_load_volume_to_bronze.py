# Databricks notebook source
# MAGIC %md
# MAGIC # ResearchMatch: load landed source files into Bronze
# MAGIC
# MAGIC Default: **validate** two ZU Scholars files without writing Delta tables.
# MAGIC Run `sql/01_create_bronze_tables.sql` before choosing **load**.
# MAGIC Use `datasets=all` and `max_files_per_dataset=0` for the complete collection.
# MAGIC Run only one copy of this loader at a time. No API key or extra packages needed.
# MAGIC See `docs/volume_to_bronze_runbook.md` for the test and full-load sequence.

# COMMAND ----------

# Pure Python adapters: these also run locally without Spark for validation/tests.
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
import uuid
import xml.etree.ElementTree as ET

DEFAULT_LANDING = "/Volumes/workspace/researchmatch_bronze/landing/"
DEFAULT_BATCHES = "pilot-20260905,openalex-20260905"
OAI_NS = "http://www.openarchives.org/OAI/2.0/"
NS = {"oai": OAI_NS}
ET.register_namespace("", OAI_NS)  # Same serialization convention as notebook 02.
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ROW_BYTES = 4 * 1024 * 1024

# ! marks NOT NULL. These contracts mirror SQL 01, with explicit Spark types.
SPECS = {
    "ingestion_runs": """ingestion_run_id:string! source_system:string! dataset_name:string!
        started_at:timestamp! completed_at:timestamp status:string! request_parameters_sanitized:string
        watermark_or_cursor_start:string watermark_or_cursor_end:string pages_requested:bigint!
        records_received:bigint! records_inserted:bigint! records_updated:bigint!
        records_unchanged:bigint! records_failed:bigint! http_status_summary:string
        error_message:string created_by:string""",
    "ingestion_errors": """error_id:string! ingestion_run_id:string! source_system:string!
        dataset_name:string! source_record_id:string request_context_sanitized:string
        error_type:string! error_message:string! raw_error_payload:string occurred_at:timestamp!""",
    "zu_faculty_pages_raw": """record_key:string! page_url:string! page_type:string!
        college_code:string http_status:int content_type:string fetched_at:timestamp!
        content_hash:string! raw_html:string! ingestion_run_id:string!""",
    "zu_faculty_discovered_profiles": """record_key:string! listing_page_url:string!
        profile_url:string! displayed_name:string personnel_section:string college_code:string
        discovered_at:timestamp! crawl_status:string ingestion_run_id:string!""",
    "zu_scholars_oai_raw": """record_key:string! oai_identifier:string! oai_datestamp:timestamp
        set_specs:array<string> is_deleted:boolean! metadata_prefix:string! response_date:timestamp
        resumption_token_used:string harvest_from:timestamp harvest_until:timestamp request_url:string!
        record_hash:string! raw_record_xml:string! fetched_at:timestamp! ingestion_run_id:string!""",
    "openalex_institutions_raw": """record_key:string! openalex_institution_id:string! ror:string
        query_reason:string! source_updated_at:timestamp fetched_at:timestamp! record_hash:string!
        request_url_without_api_key:string! raw_json:string! ingestion_run_id:string!""",
    "openalex_authors_raw": """record_key:string! openalex_author_id:string! orcid:string
        query_reason:string! source_updated_at:timestamp fetched_at:timestamp! record_hash:string!
        request_url_without_api_key:string! raw_json:string! ingestion_run_id:string!""",
    "openalex_works_raw": """record_key:string! openalex_work_id:string! doi:string
        query_reason:string! source_updated_at:timestamp fetched_at:timestamp! record_hash:string!
        request_url_without_api_key:string! raw_json:string! ingestion_run_id:string!""",
    "openalex_awards_raw": """record_key:string! openalex_award_id:string! funder_award_id:string
        openalex_funder_id:string query_reason:string! source_updated_at:timestamp fetched_at:timestamp!
        record_hash:string! request_url_without_api_key:string! raw_json:string! ingestion_run_id:string!""",
    "openalex_funders_raw": """record_key:string! openalex_funder_id:string! doi:string ror:string
        query_reason:string! source_updated_at:timestamp fetched_at:timestamp! record_hash:string!
        request_url_without_api_key:string! raw_json:string! ingestion_run_id:string!""",
}
DATASETS = tuple(name for name in SPECS if name not in ("ingestion_runs", "ingestion_errors"))
ENTITY_IDS = {
    "openalex_institutions_raw": ("openalex_institution_id", "I"),
    "openalex_authors_raw": ("openalex_author_id", "A"),
    "openalex_works_raw": ("openalex_work_id", "W"),
    "openalex_awards_raw": ("openalex_award_id", "G"),
    "openalex_funders_raw": ("openalex_funder_id", "F"),
}


def columns(table):
    return [(name, kind.rstrip("!"), not kind.endswith("!"))
            for name, kind in (field.split(":", 1) for field in SPECS[table].split())]


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def timestamp(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def source_for(table):
    return "openalex" if table.startswith("openalex_") else "zu_scholars" if table == "zu_scholars_oai_raw" else "zu_faculty"


def sanitized_url(meta):
    parsed = urlsplit(meta["request_url"])
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("Expected a public HTTPS source URL without embedded credentials")
    sensitive = {"api_key", "apikey", "access_token", "authorization", "password"}
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params.update(meta.get("request_parameters") or {})
    params = {k: v for k, v in params.items() if k.lower() not in sensitive}
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params, doseq=True), ""))


def checked_row(table, row):
    spec = columns(table)
    if set(row) != {name for name, _, _ in spec}:
        raise ValueError("Adapter columns differ from Bronze contract: " + table)
    for name, kind, nullable in spec:
        value = row[name]
        if value is None:
            if not nullable:
                raise ValueError("Missing required field: " + name)
            continue
        valid = {"string": lambda: isinstance(value, str),
                 "timestamp": lambda: isinstance(value, datetime),
                 "int": lambda: type(value) is int,
                 "bigint": lambda: type(value) is int,
                 "boolean": lambda: type(value) is bool,
                 "array<string>": lambda: isinstance(value, list) and all(isinstance(x, str) for x in value)}
        if not valid[kind]():
            raise ValueError("Invalid value type for " + name)
    if row_size(row) > MAX_ROW_BYTES:
        raise ValueError("Record exceeds 4 MiB local-row limit; use a file-based Spark adapter")
    return row


def row_size(row):
    return sum(len(value.encode("utf-8")) if isinstance(value, str) else 32 for value in row.values())


class FacultyCards(HTMLParser):
    """Parse observed ZU directory cards without installing BeautifulSoup."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.div_depth = 0
        self.card_depth = None
        self.section = ""
        self.section_text = None
        self.name_text = None
        self.card = None
        self.cards = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "h3" and "en-static-heading" in classes:
            self.section_text = []
        if tag == "div":
            self.div_depth += 1
            if "picture-block" in classes:
                self.card_depth = self.div_depth
                self.card = {"section": self.section, "name": "", "links": []}
        if self.card is not None:
            if tag == "h4" and "media-heading" in classes:
                self.name_text = []
            if tag == "a" and attrs.get("href"):
                self.card["links"].append(attrs["href"].strip())

    def handle_data(self, data):
        if self.section_text is not None:
            self.section_text.append(data)
        if self.name_text is not None:
            self.name_text.append(data)

    def handle_endtag(self, tag):
        if tag == "h3" and self.section_text is not None:
            self.section = " ".join("".join(self.section_text).split()).lower()
            self.section_text = None
        if tag == "h4" and self.name_text is not None and self.card is not None:
            self.card["name"] = " ".join("".join(self.name_text).split())
            self.name_text = None
        if tag == "div":
            if self.card is not None and self.card_depth == self.div_depth:
                self.cards.append(self.card)
                self.card = None
                self.card_depth = None
            self.div_depth -= 1


def faculty_links(text, meta, run_id):
    parser = FacultyCards()
    parser.feed(text)
    parser.close()
    if not parser.cards:
        raise ValueError("No directory cards found in a listing file")
    base = meta["request_url"]
    prefix = urlsplit(base).path.split("/faculty_and_staff/", 1)[0] + "/faculty_and_staff/"
    rows = []
    for card in parser.cards:
        profile = None
        for link in card["links"]:
            parsed = urlsplit(urljoin(base, link))
            if (parsed.scheme == "https" and parsed.hostname == urlsplit(base).hostname
                    and parsed.path.startswith(prefix) and "/_profiles/" in parsed.path
                    and not re.search(r"\.(pdf|png|jpe?g|docx?)$", parsed.path, re.I)):
                profile = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
                break
        if not profile or not card["name"]:
            # NOT NULL profile_url cannot represent '#' links. The raw HTML retains them.
            continue
        section = card["section"]
        category = ("adjunct_faculty" if "adjunct" in section else "staff" if "staff" in section
                    else "administrators" if "administrator" in section else "faculty")
        identity = [base, profile, card["name"], category, meta.get("college_code")]
        rows.append({"record_key": digest(canonical(identity)), "listing_page_url": base,
                     "profile_url": profile, "displayed_name": card["name"],
                     "personnel_section": category, "college_code": meta.get("college_code"),
                     "discovered_at": timestamp(meta["fetched_at"]), "crawl_status": "discovered",
                     "ingestion_run_id": run_id})
    return rows


def parse_rows(table, content, meta, config, run_id):
    fetched = timestamp(meta["fetched_at"])
    url = sanitized_url(meta)
    if table in ("zu_faculty_pages_raw", "zu_faculty_discovered_profiles"):
        encoding = re.search(r"charset=([\w-]+)", meta.get("content_type", ""), re.I)
        text = content.decode(encoding.group(1) if encoding else "utf-8")
        if table == "zu_faculty_discovered_profiles":
            rows = faculty_links(text, meta, run_id)
        else:
            if meta.get("page_type") not in ("listing", "profile"):
                raise ValueError("Unknown faculty page_type")
            rows = [{"record_key": digest(canonical([url, meta["sha256"]])), "page_url": url,
                     "page_type": meta["page_type"], "college_code": meta.get("college_code"),
                     "http_status": meta["http_status"], "content_type": meta.get("content_type"),
                     "fetched_at": fetched, "content_hash": meta["sha256"], "raw_html": text,
                     "ingestion_run_id": run_id}]
    elif table == "zu_scholars_oai_raw":
        root = ET.fromstring(content)
        if root.tag != "{" + OAI_NS + "}OAI-PMH":
            raise ValueError("Not an OAI-PMH response")
        errors = root.findall("oai:error", NS)
        if errors:
            if all(e.get("code") == "noRecordsMatch" for e in errors):
                if meta.get("record_count") != 0:
                    raise ValueError("Parsed record count differs from manifest")
                return []
            raise ValueError("OAI protocol error: " + ", ".join(e.get("code", "unknown") for e in errors))
        listing = root.find("oai:ListRecords", NS)
        if listing is None:
            raise ValueError("OAI response has no ListRecords")
        params = meta.get("request_parameters") or {}
        prefix = params.get("metadataPrefix") or config.get("metadata_prefix")
        if not prefix:
            raise ValueError("Missing OAI metadata prefix")
        rows = []
        for record in listing.findall("oai:record", NS):
            header = record.find("oai:header", NS)
            if header is None:
                raise ValueError("OAI record has no header")
            identifier = header.findtext("oai:identifier", namespaces=NS)
            datestamp = header.findtext("oai:datestamp", namespaces=NS)
            raw = ET.tostring(record, encoding="unicode")
            content_hash = digest(raw)
            if not identifier:
                raise ValueError("OAI record has no identifier")
            # Compatible with notebook 02 under the same ElementTree namespace setup.
            key = digest("|".join([identifier, datestamp or "", prefix, content_hash]))
            rows.append({"record_key": key, "oai_identifier": identifier,
                         "oai_datestamp": timestamp(datestamp),
                         "set_specs": [e.text.strip() for e in header.findall("oai:setSpec", NS) if e.text and e.text.strip()],
                         "is_deleted": header.get("status") == "deleted", "metadata_prefix": prefix,
                         "response_date": timestamp(root.findtext("oai:responseDate", namespaces=NS)),
                         "resumption_token_used": params.get("resumptionToken"),
                         "harvest_from": timestamp(params.get("from")), "harvest_until": timestamp(params.get("until")),
                         "request_url": url, "record_hash": content_hash, "raw_record_xml": raw,
                         "fetched_at": fetched, "ingestion_run_id": run_id})
    else:
        data = json.loads(content)
        records = data["results"] if "results" in data else [data]
        if not isinstance(records, list):
            raise ValueError("OpenAlex results must be a list")
        id_column, id_prefix = ENTITY_IDS[table]
        rows = []
        for record in records:
            identifier = record.get("id")
            if not isinstance(identifier, str) or not re.fullmatch(r"https://openalex\.org/" + id_prefix + r"\d+", identifier):
                raise ValueError("Missing or wrong OpenAlex entity ID")
            raw = canonical(record)
            content_hash = digest(raw)
            row = {"record_key": digest(canonical([table, identifier, content_hash])), id_column: identifier,
                   "query_reason": meta.get("query_reason"), "source_updated_at": timestamp(record.get("updated_date")),
                   "fetched_at": fetched, "record_hash": content_hash, "request_url_without_api_key": url,
                   "raw_json": raw, "ingestion_run_id": run_id}
            if table == "openalex_institutions_raw":
                row["ror"] = record.get("ror")
            elif table == "openalex_authors_raw":
                row["orcid"] = record.get("orcid")
            elif table == "openalex_works_raw":
                row["doi"] = record.get("doi")
            elif table == "openalex_awards_raw":
                row["funder_award_id"] = record.get("funder_award_id")
                row["openalex_funder_id"] = (record.get("funder") or {}).get("id")
            elif table == "openalex_funders_raw":
                row.update(doi=record.get("doi"), ror=record.get("ror"))
            rows.append(row)
    if table in ENTITY_IDS or table == "zu_scholars_oai_raw":
        if meta.get("record_count") != len(rows):
            raise ValueError("Parsed record count differs from manifest")
    return [checked_row(table, row) for row in rows]

# COMMAND ----------

def select_inputs(landing, batch_names, datasets, max_files):
    if max_files < 0:
        raise ValueError("max_files_per_dataset must be zero or positive")
    datasets = list(DATASETS) if datasets == ["all"] else datasets
    if not datasets or any(d not in DATASETS for d in datasets) or len(set(datasets)) != len(datasets):
        raise ValueError("Choose unique dataset names from: " + ", ".join(DATASETS))
    if len(set(batch_names)) != len(batch_names):
        raise ValueError("Batch folders must be unique")
    root = Path(landing).resolve()
    refs = {d: [] for d in datasets}
    for batch in batch_names:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", batch):
            raise ValueError("Expected a batch folder name, not a path")
        path = root / batch
        if not path.resolve().is_relative_to(root):
            raise ValueError("Batch path escapes the landing root")
        manifest_path = path / "manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        if manifest.get("format_version") != 1 or manifest.get("batch_id") != batch:
            raise ValueError("Unsupported manifest format or mismatched batch ID: " + batch)
        for table in datasets:
            source = "openalex" if table.startswith("openalex") else "oai" if table == "zu_scholars_oai_raw" else "faculty"
            raw_dataset = "zu_faculty_pages_raw" if table == "zu_faculty_discovered_profiles" else table
            entries = [meta for meta in manifest["files"].values() if meta["dataset"] == raw_dataset
                       and (table != "zu_faculty_discovered_profiles" or meta.get("page_type") == "listing")]
            if entries and manifest["sources"].get(source, {}).get("status") != "complete":
                raise ValueError("Source batch is not complete: " + batch + "/" + source)
            for meta in entries:
                file_path = path / meta["path"]
                if (Path(meta["path"]).is_absolute()
                        or not file_path.resolve().is_relative_to(path.resolve())
                        or not file_path.resolve().is_relative_to((path / "raw").resolve())):
                    raise ValueError("Manifest payload path escapes the raw directory")
                if not re.fullmatch(r"[0-9a-f]{64}", meta["sha256"]):
                    raise ValueError("Invalid file SHA-256 in manifest")
                if type(meta["bytes"]) is not int or not 0 <= meta["bytes"] <= MAX_FILE_BYTES:
                    raise ValueError("File size exceeds the 32 MiB pilot limit")
                if meta.get("http_status") != 200:
                    raise ValueError("Manifest references an unsuccessful response")
                refs[table].append({"file_path": str(file_path), "meta": meta,
                                    "config": manifest["config"], "batch_id": batch,
                                    "manifest_path": str(manifest_path), "manifest_sha256": digest(manifest_bytes)})
    result = {}
    for table, files in refs.items():
        if not files:
            raise ValueError("No manifested source files found for " + table)
        selected = files[:max_files] if max_files else files
        result[table] = {"files": selected, "available_files": len(files), "bounded": len(selected) < len(files)}
    return result


def read_rows(table, ref, run_id):
    with Path(ref["file_path"]).open("rb") as source:
        content = source.read(MAX_FILE_BYTES + 1)
    meta = ref["meta"]
    if len(content) != meta["bytes"] or digest(content) != meta["sha256"]:
        raise ValueError("File size/checksum differs from the uploaded manifest")
    return parse_rows(table, content, meta, ref["config"], run_id)


def validate_inputs(plan):
    summary = {}
    errors = []
    for table, selection in plan.items():
        versions = set()
        received = 0
        for ref in selection["files"]:
            try:
                rows = read_rows(table, ref, "validation")
                received += len(rows)
                versions.update(r["record_key"] for r in rows)
            except Exception as exc:
                errors.append({"dataset": table, "file_path": ref["file_path"], "message": str(exc)[:2000]})
        summary[table] = {"selected_files": len(selection["files"]), "available_files": selection["available_files"],
                          "bounded": selection["bounded"], "record_occurrences": received,
                          "unique_versions": len(versions), "duplicate_occurrences": received - len(versions)}
    return summary, errors


def unique_chunks(rows, max_rows=1000, max_bytes=8 * 1024 * 1024):
    """Keep only small row buffers and a set of hashes; never retain all raw data."""
    seen, buffer, size = set(), [], 0
    for row in rows:
        if row["record_key"] in seen:
            continue
        nbytes = row_size(row)
        if buffer and (len(buffer) >= max_rows or size + nbytes > max_bytes):
            yield buffer
            buffer, size = [], 0
        seen.add(row["record_key"])
        buffer.append(row)
        size += nbytes
    if buffer:
        yield buffer

# COMMAND ----------

def spark_schema(table):
    from pyspark.sql.types import StructType, StructField, StringType, TimestampType, LongType, IntegerType, BooleanType, ArrayType
    types = {"string": StringType(), "timestamp": TimestampType(), "bigint": LongType(),
             "int": IntegerType(), "boolean": BooleanType(), "array<string>": ArrayType(StringType())}
    return StructType([StructField(name, types[kind], nullable) for name, kind, nullable in columns(table)])


def quoted_table(catalog, schema, table):
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) for value in (catalog, schema, table)):
        raise ValueError("Invalid catalog, schema, or table identifier")
    return ".".join("`" + value + "`" for value in (catalog, schema, table))


def check_tables(spark, catalog, schema, datasets):
    for table in ["ingestion_runs", "ingestion_errors", *datasets]:
        name = quoted_table(catalog, schema, table)
        if not spark.catalog.tableExists(name):
            raise ValueError("Run SQL 01 first; missing table: " + name)
        actual = {f.name: f.dataType.simpleString() for f in spark.table(name).schema.fields}
        expected = {field: kind for field, kind, _ in columns(table)}
        if actual != expected:
            raise ValueError("Existing table schema differs from SQL 01: " + name)
        if spark.sql("DESCRIBE DETAIL " + name).first()["format"] != "delta":
            raise ValueError("Expected a Delta table: " + name)


def append_control(spark, catalog, schema, table, rows):
    spark.createDataFrame(rows, schema=spark_schema(table)).write.mode("append").saveAsTable(quoted_table(catalog, schema, table))


def merge_rows(spark, target, table, rows, control=False):
    view = "researchmatch_load_" + uuid.uuid4().hex
    spark.createDataFrame(rows, schema=spark_schema(table)).createOrReplaceTempView(view)
    key = "ingestion_run_id" if control else "record_key"
    action = "WHEN MATCHED THEN UPDATE SET *" if control else "WHEN NOT MATCHED THEN INSERT *"
    try:
        spark.sql(f"MERGE INTO {target} AS t USING `{view}` AS s ON t.{key} = s.{key} {action}").collect()
    finally:
        spark.catalog.dropTempView(view)


def new_run(table, selection, created_by):
    row = {name: None for name, _, _ in columns("ingestion_runs")}
    files = [{"file_path": r["file_path"], "sha256": r["meta"]["sha256"],
              "manifest_path": r["manifest_path"], "manifest_sha256": r["manifest_sha256"]} for r in selection["files"]]
    row.update(ingestion_run_id=str(uuid.uuid4()), source_system=source_for(table), dataset_name=table,
               started_at=utc_now(), status="started", created_by=created_by,
               request_parameters_sanitized=canonical({"loader": "volume_bronze_v1", "bounded": selection["bounded"],
                                                       "available_files": selection["available_files"], "files": files}),
               pages_requested=0, records_received=0, records_inserted=0, records_updated=0,
               records_unchanged=0, records_failed=0,
               http_status_summary=canonical({"note": "Volume reads only; no HTTP requests"}))
    return row


def error_row(run, message, context, error_type):
    return {"error_id": str(uuid.uuid4()), "ingestion_run_id": run["ingestion_run_id"],
            "source_system": run["source_system"], "dataset_name": run["dataset_name"], "source_record_id": None,
            "request_context_sanitized": canonical(context), "error_type": error_type,
            "error_message": str(message)[:4000], "raw_error_payload": None, "occurred_at": utc_now()}


def load_plan(spark, catalog, schema, plan, validation_errors, max_rows, max_bytes):
    from pyspark.sql import functions as F
    created_by = spark.sql("SELECT current_user() AS user").first()["user"]
    runs_target = quoted_table(catalog, schema, "ingestion_runs")
    if validation_errors:
        for table in {e["dataset"] for e in validation_errors}:
            run = new_run(table, plan[table], created_by)
            run.update(status="failed", completed_at=utc_now(), error_message="Preflight failed; no business tables written")
            append_control(spark, catalog, schema, "ingestion_runs", [run])
            append_control(spark, catalog, schema, "ingestion_errors", [
                error_row(run, e["message"], {"file_path": e["file_path"]}, "preflight_error")
                for e in validation_errors if e["dataset"] == table])
        raise RuntimeError("File validation failed; inspect ingestion_errors. No business tables were written.")
    results = []
    for table, selection in plan.items():
        run = new_run(table, selection, created_by)
        append_control(spark, catalog, schema, "ingestion_runs", [run])
        target = quoted_table(catalog, schema, table)
        context = {"target": target}
        failed = None
        try:
            def records():
                for ref in selection["files"]:
                    context["file_path"] = ref["file_path"]
                    rows = read_rows(table, ref, run["ingestion_run_id"])
                    run["records_received"] += len(rows)
                    yield from rows
            for chunk in unique_chunks(records(), max_rows, max_bytes):
                merge_rows(spark, target, table, chunk)
            run["status"] = "partial" if selection["bounded"] else "succeeded"
        except Exception as exc:
            failed = exc
            run.update(status="failed", error_message=str(exc)[:4000])
            append_control(spark, catalog, schema, "ingestion_errors", [error_row(run, exc, context, "load_error")])
        finally:
            # Existing rows retain their original run ID; this counts only this run's inserts.
            # One metadata/count action per dataset, not one per record or source page.
            try:
                run["records_inserted"] = spark.table(target).where(F.col("ingestion_run_id") == run["ingestion_run_id"]).count()
                if failed is None:
                    run["records_unchanged"] = run["records_received"] - run["records_inserted"]
            except Exception as exc:
                failed = failed or exc
                run.update(status="failed", error_message="Final insert count unavailable: " + str(exc)[:2000])
            run["completed_at"] = utc_now()
            merge_rows(spark, runs_target, "ingestion_runs", [run], control=True)
        result = {k: run[k] for k in ("ingestion_run_id", "dataset_name", "status", "records_received", "records_inserted", "records_unchanged")}
        results.append(result)
        print(json.dumps(result))
        if failed:
            raise RuntimeError("Load failed; earlier committed versions are retained. Fix the error and rerun the same selection.") from failed
    return results

# COMMAND ----------

def run_notebook(spark, dbutils):
    dbutils.widgets.text("landing_path", DEFAULT_LANDING, "01 Landing Volume path")
    dbutils.widgets.text("batch_folders", DEFAULT_BATCHES, "02 Batch folders, comma-separated")
    dbutils.widgets.text("catalog", "workspace", "03 Target catalog")
    dbutils.widgets.text("bronze_schema", "researchmatch_bronze", "04 Target schema")
    dbutils.widgets.text("datasets", "zu_scholars_oai_raw", "05 Datasets: all or comma-separated names")
    dbutils.widgets.dropdown("mode", "validate", ["validate", "load"], "06 Mode")
    dbutils.widgets.text("max_files_per_dataset", "2", "07 File limit; 0 = all")
    dbutils.widgets.text("write_batch_rows", "1000", "08 Rows per Delta merge")
    dbutils.widgets.text("write_batch_mib", "8", "09 Approximate payload MiB per merge")
    get = lambda name: dbutils.widgets.get(name).strip()
    landing = get("landing_path")
    if not landing.startswith("/Volumes/"):
        raise ValueError("Use an absolute /Volumes/<catalog>/<schema>/<volume>/ path")
    mode = get("mode")
    max_rows, max_bytes = int(get("write_batch_rows")), int(get("write_batch_mib")) * 1024 * 1024
    if mode not in ("validate", "load") or not 1 <= max_rows <= 5000 or not MAX_ROW_BYTES <= max_bytes <= 16 * 1024 * 1024:
        raise ValueError("Use validate/load, 1–5000 rows, and 4–16 MiB write buffers")
    plan = select_inputs(landing, [x.strip() for x in get("batch_folders").split(",")],
                         [x.strip() for x in get("datasets").split(",")], int(get("max_files_per_dataset")))
    if mode == "load":
        check_tables(spark, get("catalog"), get("bronze_schema"), plan)
        spark.conf.set("spark.sql.session.timeZone", "UTC")
    summary, errors = validate_inputs(plan)
    print(json.dumps({"mode": mode, "validation": summary, "errors": errors}, indent=2))
    if mode == "validate":
        if errors:
            raise RuntimeError("Validation failed. Correct the listed files before loading.")
        print("Validation passed. No Delta tables were written. Set mode=load for this selection.")
        return summary
    return load_plan(spark, get("catalog"), get("bronze_schema"), plan, errors, max_rows, max_bytes)


if "spark" in globals() and "dbutils" in globals():
    load_result = run_notebook(spark, dbutils)
elif __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Validate local landed data without Spark or Delta writes")
    parser.add_argument("--landing-path", required=True)
    parser.add_argument("--batch-folders", default=DEFAULT_BATCHES)
    parser.add_argument("--datasets", default="all")
    parser.add_argument("--max-files-per-dataset", type=int, default=0)
    args = parser.parse_args()
    plan = select_inputs(args.landing_path, args.batch_folders.split(","), args.datasets.split(","), args.max_files_per_dataset)
    summary, errors = validate_inputs(plan)
    print(json.dumps({"validation": summary, "errors": errors}, indent=2))
    raise SystemExit(1 if errors else 0)
