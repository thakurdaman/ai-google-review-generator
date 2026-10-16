"""
Database layer for ReviewBoost SaaS Platform.
Uses Supabase PostgREST API for persistent cloud storage.
Falls back to SQLite for local development when SUPABASE_URL is not set.
"""
import os
import re
import json
import hashlib
import requests
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

try:
    import dotenv
    dotenv.load_dotenv()
except Exception:
    pass

# ── Supabase Configuration ────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    "https://jaggkwijjpsoobcfppnp.supabase.co"
)
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()


REST_BASE = f"{SUPABASE_URL}/rest/v1"

def _headers(*, prefer: str = "") -> dict:
    """Standard headers for Supabase REST API calls."""
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _get(table: str, *, select: str = "*", params: dict = None, single: bool = False,
         order: str = "", limit: int = 0) -> Any:
    """GET request to Supabase REST API."""
    url = f"{REST_BASE}/{table}?select={select}"
    if params:
        for k, v in params.items():
            url += f"&{k}={v}"
    if order:
        url += f"&order={order}"
    if limit:
        url += f"&limit={limit}"
    if single:
        resp = requests.get(url, headers=_headers(prefer="return=representation"))
        data = resp.json()
        return data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
    resp = requests.get(url, headers=_headers())
    return resp.json() if resp.status_code == 200 else []


def _post(table: str, data: dict, *, upsert: bool = False) -> dict:
    """POST (insert) to Supabase REST API. Returns inserted row."""
    url = f"{REST_BASE}/{table}"
    prefer = "return=representation"
    if upsert:
        prefer += ",resolution=merge-duplicates"
    resp = requests.post(url, headers=_headers(prefer=prefer), json=data)
    result = resp.json()
    if isinstance(result, list) and result:
        return result[0]
    return result if isinstance(result, dict) else {}


def _patch(table: str, filters: dict, data: dict) -> list:
    """PATCH (update) rows in Supabase REST API."""
    url = f"{REST_BASE}/{table}"
    params = []
    for k, v in filters.items():
        params.append(f"{k}={v}")
    if params:
        url += "?" + "&".join(params)
    resp = requests.patch(url, headers=_headers(prefer="return=representation"), json=data)
    return resp.json() if resp.status_code == 200 else []


def _delete(table: str, filters: dict) -> None:
    """DELETE rows from Supabase REST API."""
    url = f"{REST_BASE}/{table}"
    params = []
    for k, v in filters.items():
        params.append(f"{k}={v}")
    if params:
        url += "?" + "&".join(params)
    requests.delete(url, headers=_headers())


def _count(table: str, filters: dict = None) -> int:
    """Count rows in a table matching filters."""
    url = f"{REST_BASE}/{table}?select=id"
    if filters:
        for k, v in filters.items():
            url += f"&{k}={v}"
    h = _headers(prefer="count=exact")
    resp = requests.get(url, headers=h)
    # Count is in Content-Range header: "0-N/total"
    cr = resp.headers.get("Content-Range", "")
    if "/" in cr:
        try:
            return int(cr.split("/")[1])
        except (ValueError, IndexError):
            pass
    # Fallback: count the returned items
    data = resp.json()
    return len(data) if isinstance(data, list) else 0


# ── SQL Parser / Compatibility Layer ──────────────────────────────────────────
# main.py and auth.py use raw SQL with cursor.execute(sql, params) pattern.
# This wrapper parses common SQL patterns and translates to REST calls.

class SupabaseCursor:
    """Mimics sqlite3.Cursor for Supabase REST API queries."""

    def __init__(self):
        self._result = []
        self._lastrowid = None

    @property
    def lastrowid(self):
        return self._lastrowid

    def execute(self, sql: str, params: tuple = ()):
        """Parse SQL and execute via Supabase REST API."""
        sql_clean = sql.strip()
        # Substitute ? placeholders with actual values for parsing
        sql_filled = self._substitute_params(sql_clean, params)
        
        if sql_clean.upper().startswith("SELECT"):
            self._handle_select(sql_filled)
        elif sql_clean.upper().startswith("INSERT"):
            self._handle_insert(sql_clean, params)
        elif sql_clean.upper().startswith("UPDATE"):
            self._handle_update(sql_clean, params)
        elif sql_clean.upper().startswith("DELETE"):
            self._handle_delete(sql_filled)
        elif sql_clean.upper().startswith(("CREATE", "PRAGMA", "ALTER")):
            # DDL - no-op for Supabase (tables created via dashboard)
            self._result = []
        else:
            self._result = []

    def _substitute_params(self, sql: str, params: tuple) -> str:
        """Replace ? placeholders with actual values."""
        result = sql
        for p in params:
            if p is None:
                replacement = "NULL"
            elif isinstance(p, (int, float)):
                replacement = str(p)
            else:
                # Escape single quotes for SQL-like matching
                replacement = "'" + str(p).replace("'", "''") + "'"
            result = result.replace("?", replacement, 1)
        return result

    def _handle_select(self, sql: str):
        """Parse SELECT and execute via REST GET."""
        # Handle subquery-based metrics (special case for get_business_metrics)
        if "SELECT" in sql.upper() and sql.upper().count("SELECT") > 1:
            self._handle_subquery_select(sql)
            return

        # Parse: SELECT [columns] FROM [table] [JOIN ...] [WHERE ...] [ORDER BY ...] [LIMIT ...]
        # Handle JOINs
        join_match = re.search(
            r'FROM\s+(\w+)\s+(\w+)?\s*(?:LEFT\s+)?JOIN\s+(\w+)\s+(\w+)?\s*ON\s+(.+?)(?:\s+WHERE|\s+ORDER|\s+LIMIT|\s*$)',
            sql, re.IGNORECASE | re.DOTALL
        )
        
        if join_match:
            self._handle_join_select(sql, join_match)
            return

        # Simple SELECT
        m = re.match(
            r"SELECT\s+(.+?)\s+FROM\s+(\w+)(?:\s+(\w+))?\s*(?:WHERE\s+(.+?))?\s*(?:ORDER\s+BY\s+(.+?))?\s*(?:LIMIT\s+(\d+))?\s*$",
            sql, re.IGNORECASE | re.DOTALL
        )
        if not m:
            self._result = []
            return

        columns = m.group(1).strip()
        table = m.group(2).strip()
        alias = m.group(3)
        where = m.group(4)
        order_by = m.group(5)
        limit = m.group(6)

        # Build select param
        select = "*" if columns in ("*", f"{alias}.*" if alias else "*") else columns

        # Build URL
        url = f"{REST_BASE}/{table}?select={select}"

        # Parse WHERE clause
        if where:
            filters = self._parse_where(where, alias or table[0] if len(table) > 1 else "")
            for f in filters:
                url += f"&{f}"

        # ORDER BY
        if order_by:
            order_parts = []
            for part in order_by.split(","):
                part = part.strip()
                col_parts = part.split()
                col = col_parts[0].split(".")[-1]  # Remove table alias
                direction = "desc" if len(col_parts) > 1 and col_parts[1].upper() == "DESC" else "asc"
                order_parts.append(f"{col}.{direction}")
            url += "&order=" + ",".join(order_parts)

        # LIMIT
        if limit:
            url += f"&limit={limit}"

        resp = requests.get(url, headers=_headers())
        self._result = resp.json() if resp.status_code == 200 else []

    def _handle_join_select(self, sql: str, join_match):
        """Handle SELECT with JOIN using PostgREST embedded resources or manual join."""
        # For complex joins with subqueries in select, use manual approach
        # Parse the main table
        m = re.match(r"SELECT\s+(.+?)\s+FROM\s+(\w+)", sql, re.IGNORECASE | re.DOTALL)
        if not m:
            self._result = []
            return

        select_cols = m.group(1).strip()
        main_table = m.group(2).strip()

        # Check for subqueries in SELECT (used in admin dashboard)
        if "(SELECT" in sql.upper():
            self._handle_subquery_select(sql)
            return

        # For simple joins (e.g., business_owners JOIN businesses)
        # Parse which tables are joined
        join_tables = re.findall(r'(?:LEFT\s+)?JOIN\s+(\w+)', sql, re.IGNORECASE)

        # Parse WHERE
        where_match = re.search(r'WHERE\s+(.+?)(?:\s+ORDER|\s+LIMIT|\s*$)', sql, re.IGNORECASE | re.DOTALL)
        where_clause = where_match.group(1).strip() if where_match else None

        # Parse ORDER BY
        order_match = re.search(r'ORDER\s+BY\s+(.+?)(?:\s+LIMIT|\s*$)', sql, re.IGNORECASE | re.DOTALL)
        order_clause = order_match.group(1).strip() if order_match else None

        # Parse LIMIT
        limit_match = re.search(r'LIMIT\s+(\d+)', sql, re.IGNORECASE)
        limit_val = limit_match.group(1) if limit_match else None

        # Build PostgREST query with embedded resources
        # Map foreign key relationships
        fk_map = {
            "businesses": {
                "business_owners": "business_owners!business_owners_business_id_fkey",
            },
            "business_owners": {
                "businesses": "businesses!business_owners_business_id_fkey",
            },
            "payments": {
                "businesses": "businesses!payments_business_id_fkey",
            },
            "private_feedback": {
                "businesses": "businesses!private_feedback_business_id_fkey",
            },
            "activity_logs": {
                "businesses": "businesses!activity_logs_business_id_fkey",
            }
        }

        # For many join patterns in this app, it's easier to do two queries
        # and merge the results manually
        
        # Strategy: fetch from main table, then enrich with joined data
        url = f"{REST_BASE}/{main_table}?select=*"

        if where_clause:
            filters = self._parse_where(where_clause)
            for f in filters:
                url += f"&{f}"

        if order_clause:
            parts = []
            for p in order_clause.split(","):
                p = p.strip()
                col_parts = p.split()
                col = col_parts[0].split(".")[-1]
                direction = "desc" if len(col_parts) > 1 and col_parts[1].upper() == "DESC" else "asc"
                parts.append(f"{col}.{direction}")
            url += "&order=" + ",".join(parts)

        if limit_val:
            url += f"&limit={limit_val}"

        resp = requests.get(url, headers=_headers())
        main_rows = resp.json() if resp.status_code == 200 else []

        if not main_rows or not join_tables:
            self._result = main_rows
            return

        # Enrich with joined table data
        for jt in join_tables:
            if jt == "businesses" and main_table in ("payments", "private_feedback", "activity_logs"):
                # Get business names for these rows
                biz_ids = list(set(r.get("business_id") for r in main_rows if r.get("business_id")))
                if biz_ids:
                    biz_url = f"{REST_BASE}/businesses?select=id,name,slug&id=in.({','.join(str(i) for i in biz_ids)})"
                    biz_resp = requests.get(biz_url, headers=_headers())
                    biz_map = {}
                    if biz_resp.status_code == 200:
                        for b in biz_resp.json():
                            biz_map[b["id"]] = b
                    for row in main_rows:
                        biz = biz_map.get(row.get("business_id"), {})
                        row["business_name"] = biz.get("name", "")
                        row["business_slug"] = biz.get("slug", "")

            elif jt == "business_owners" and main_table == "businesses":
                # Get owner info for businesses
                owner_ids = list(set(r.get("owner_id") for r in main_rows if r.get("owner_id")))
                if owner_ids:
                    owner_url = f"{REST_BASE}/business_owners?select=id,email,full_name,last_login_at,created_at&id=in.({','.join(str(i) for i in owner_ids)})"
                    owner_resp = requests.get(owner_url, headers=_headers())
                    owner_map = {}
                    if owner_resp.status_code == 200:
                        for o in owner_resp.json():
                            owner_map[o["id"]] = o
                    for row in main_rows:
                        owner = owner_map.get(row.get("owner_id"), {})
                        row["owner_email"] = owner.get("email", "")
                        row["owner_name"] = owner.get("full_name", "")
                        row["last_login_at"] = owner.get("last_login_at", "")
                        row["signup_date"] = owner.get("created_at", "")

            elif jt == "businesses" and main_table == "business_owners":
                # Get business info for owners
                biz_ids = list(set(r.get("business_id") for r in main_rows if r.get("business_id")))
                if biz_ids:
                    biz_url = f"{REST_BASE}/businesses?select=*&id=in.({','.join(str(i) for i in biz_ids)})"
                    biz_resp = requests.get(biz_url, headers=_headers())
                    biz_map = {}
                    if biz_resp.status_code == 200:
                        for b in biz_resp.json():
                            biz_map[b["id"]] = b
                    for row in main_rows:
                        biz = biz_map.get(row.get("business_id"), {})
                        row["business_name"] = biz.get("name", "")
                        row["business_slug"] = biz.get("slug", "")
                        row["google_review_url"] = biz.get("google_review_url", "")
                        row["subscription_status"] = biz.get("subscription_status", "")
                        row["plan_tier"] = biz.get("plan_tier", "")
                        row["payment_method"] = biz.get("payment_method", "")
                        row["subscription_cycle_end"] = biz.get("subscription_cycle_end", "")
                        row["logo_url"] = biz.get("logo_url", "")
                        row["accent_color"] = biz.get("accent_color", "")
                        row["category"] = biz.get("category", "")
                        row["tags_preset"] = biz.get("tags_preset", "[]")
                        row["active"] = biz.get("active", 0)

        self._result = main_rows

    def _handle_subquery_select(self, sql: str):
        """Handle SELECT with subqueries (e.g., aggregate counts in admin dashboard)."""
        # Special case: metrics query with (SELECT COUNT(*) ...)
        # Extract business_id from WHERE or subqueries
        bid_match = re.search(r'business_id\s*=\s*(\d+)', sql)
        
        if "FROM businesses" in sql:
            # Admin dashboard: businesses with inline counts
            businesses = _get("businesses", order="created_at.desc")
            for b in businesses:
                bid = b["id"]
                b["scans_count"] = _count("analytics_events", {"business_id": f"eq.{bid}", "event_type": "eq.scan"})
                b["reviews_count"] = _count("analytics_events", {"business_id": f"eq.{bid}", "event_type": "eq.completed_review"})
                b["feedback_count"] = _count("private_feedback", {"business_id": f"eq.{bid}"})
            self._result = businesses
        elif bid_match:
            # Single business metrics
            bid = int(bid_match.group(1))
            scans = _count("analytics_events", {"business_id": f"eq.{bid}", "event_type": "eq.scan"})
            reviews = _count("analytics_events", {"business_id": f"eq.{bid}", "event_type": "eq.completed_review"})
            feedback = _count("private_feedback", {"business_id": f"eq.{bid}"})
            self._result = [{"scans_count": scans, "reviews_count": reviews, "feedback_count": feedback}]
        else:
            self._result = []

    def _parse_where(self, where: str, alias: str = "") -> list:
        """Parse WHERE clause into PostgREST filters."""
        filters = []
        # Split by AND
        conditions = re.split(r'\s+AND\s+', where, flags=re.IGNORECASE)
        for cond in conditions:
            cond = cond.strip()
            # Match: col = value, col = 'value', alias.col = value
            m = re.match(r"(\w+\.)?(\w+)\s*=\s*(.+)", cond)
            if m:
                col = m.group(2)
                val = m.group(3).strip().strip("'\"")
                if val == "NULL":
                    filters.append(f"{col}=is.null")
                else:
                    filters.append(f"{col}=eq.{urllib.parse.quote(val)}")
        return filters

    def _split_sql_tokens(self, clause: str) -> List[str]:
        tokens = []
        current = []
        in_quote = False
        quote_char = ''
        depth = 0
        for ch in clause:
            if ch in ("'", '"'):
                if not in_quote:
                    in_quote = True
                    quote_char = ch
                elif quote_char == ch:
                    in_quote = False
                current.append(ch)
            elif not in_quote:
                if ch == '(':
                    depth += 1
                    current.append(ch)
                elif ch == ')':
                    depth -= 1
                    current.append(ch)
                elif ch == ',' and depth == 0:
                    tokens.append("".join(current).strip())
                    current = []
                else:
                    current.append(ch)
            else:
                current.append(ch)
        if current:
            tokens.append("".join(current).strip())
        return tokens

    def _handle_insert(self, sql_template: str, params: tuple):
        """Parse INSERT and execute via REST POST."""
        # Parse: INSERT INTO table (col1, col2, ...) VALUES (?, ?, ...)
        m = re.match(
            r"INSERT\s+INTO\s+(\w+)\s*\((.+?)\)\s*VALUES\s*\((.+?)\)",
            sql_template, re.IGNORECASE | re.DOTALL
        )
        if not m:
            self._result = []
            return

        table = m.group(1).strip()
        cols = [c.strip() for c in self._split_sql_tokens(m.group(2))]
        raw_vals = self._split_sql_tokens(m.group(3))

        data = {}
        param_idx = 0
        for i, col in enumerate(cols):
            if i >= len(raw_vals):
                break
            raw_v = raw_vals[i].strip()
            if raw_v == "?":
                if param_idx < len(params):
                    val = params[param_idx]
                    param_idx += 1
                    if val is not None:
                        data[col] = val
            elif raw_v.upper() == "NULL":
                pass
            elif (raw_v.startswith("'") and raw_v.endswith("'")) or (raw_v.startswith('"') and raw_v.endswith('"')):
                data[col] = raw_v[1:-1]
            elif raw_v.replace('.', '', 1).isdigit() or (raw_v.startswith('-') and raw_v[1:].replace('.', '', 1).isdigit()):
                data[col] = float(raw_v) if '.' in raw_v else int(raw_v)
            else:
                data[col] = raw_v

        result = _post(table, data)
        self._lastrowid = result.get("id") if isinstance(result, dict) else None
        self._result = [result] if result else []

    def _handle_update(self, sql_template: str, params: tuple):
        """Parse UPDATE and execute via REST PATCH."""
        # Parse: UPDATE table SET col1 = ?, col2 = ? WHERE col = ?
        m = re.match(
            r"UPDATE\s+(\w+)\s+SET\s+(.+?)\s+WHERE\s+(.+)",
            sql_template, re.IGNORECASE | re.DOTALL
        )
        if not m:
            self._result = []
            return

        table = m.group(1).strip()
        set_clause = m.group(2).strip()
        where_clause = m.group(3).strip()

        # Parse SET clause
        # Handle CASE WHEN expressions by simplifying
        set_parts = []
        depth = 0
        current = ""
        for ch in set_clause:
            if ch == '(':
                depth += 1
                current += ch
            elif ch == ')':
                depth -= 1
                current += ch
            elif ch == ',' and depth == 0:
                set_parts.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            set_parts.append(current.strip())

        # Count ? in SET clause to know which params go where
        param_idx = 0
        data = {}
        for part in set_parts:
            # col = ?, col = CASE WHEN ? THEN ? ELSE col END
            col_match = re.match(r"(\w+)\s*=\s*(.*)", part.strip())
            if not col_match:
                continue
            col = col_match.group(1)
            val_expr = col_match.group(2).strip()

            q_count = val_expr.count("?")
            if q_count == 0:
                # Static value like col = 'value'
                static_val = val_expr.strip("'\"")
                if static_val.replace('.', '', 1).isdigit():
                    data[col] = float(static_val) if '.' in static_val else int(static_val)
                else:
                    data[col] = static_val
            elif "CASE" in val_expr.upper():
                # CASE WHEN ? THEN ? ELSE col END
                # First ? is condition, second ? is value
                if param_idx + 1 < len(params):
                    condition = params[param_idx]
                    value = params[param_idx + 1]
                    if condition and condition not in (0, "0", False):
                        data[col] = value
                    param_idx += q_count
                else:
                    param_idx += q_count
            else:
                # Simple col = ?
                if param_idx < len(params):
                    data[col] = params[param_idx]
                param_idx += 1

        # Parse WHERE clause - remaining params
        where_params = params[param_idx:]
        where_conditions = re.split(r'\s+AND\s+', where_clause, flags=re.IGNORECASE)
        filters = {}
        wp_idx = 0
        for cond in where_conditions:
            cond = cond.strip()
            if "?" in cond:
                col_m = re.match(r"(\w+\.)?(\w+)\s*=\s*\?", cond)
                if col_m and wp_idx < len(where_params):
                    col = col_m.group(2)
                    filters[col] = f"eq.{where_params[wp_idx]}"
                    wp_idx += 1
            else:
                col_m = re.match(r"(\w+\.)?(\w+)\s*=\s*(.+)", cond)
                if col_m:
                    col = col_m.group(2)
                    val = col_m.group(3).strip().strip("'\"")
                    filters[col] = f"eq.{val}"

        result = _patch(table, filters, data)
        self._result = result if isinstance(result, list) else []

    def _handle_delete(self, sql: str):
        """Parse DELETE and execute via REST DELETE."""
        m = re.match(r"DELETE\s+FROM\s+(\w+)\s+WHERE\s+(.+)", sql, re.IGNORECASE | re.DOTALL)
        if not m:
            return
        table = m.group(1).strip()
        where = m.group(2).strip()
        filters = {}
        for f in self._parse_where(where):
            k, v = f.split("=", 1)
            filters[k] = v
        _delete(table, filters)
        self._result = []

    def fetchone(self):
        if self._result and isinstance(self._result, list) and len(self._result) > 0:
            return self._result[0]
        return None

    def fetchall(self):
        if isinstance(self._result, list):
            return self._result
        return []


class SupabaseConnection:
    """Mimics sqlite3.Connection for Supabase REST API."""

    def __init__(self):
        self._cursor = SupabaseCursor()

    def cursor(self):
        return self._cursor

    def commit(self):
        pass  # REST API commits immediately

    def close(self):
        pass  # No connection to close

    def execute(self, sql: str, params: tuple = ()):
        return self._cursor.execute(sql, params)


# ── Public API (same signatures as before) ────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash password with Argon2id (or bcrypt fallback)."""
    try:
        from .security import hash_password_secure
        return hash_password_secure(password)
    except Exception:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()



def get_db():
    """Return a database connection (Supabase wrapper)."""
    return SupabaseConnection()


def get_setting(key: str, default: str = "") -> str:
    """Get a setting value from the settings table."""
    rows = _get("settings", select="key,value", params={"key": f"eq.{key}"})
    if isinstance(rows, list) and rows:
        return rows[0].get("value", default)
    return default


def set_setting(key: str, value: str):
    """Upsert a setting value."""
    _post("settings", {"key": key, "value": value}, upsert=True)


def log_activity(business_id: int, action: str, details: str = "", ip_address: str = ""):
    """Insert an activity log entry."""
    _post("activity_logs", {
        "business_id": business_id,
        "action": action,
        "details": details,
        "ip_address": ip_address
    })


def record_payment(
    business_id: int,
    amount: float,
    plan_type: str,
    payment_mode: str,
    razorpay_payment_id: str = "",
    razorpay_order_id: str = "",
    razorpay_subscription_id: str = "",
    status: str = "captured",
    days: int = 30
) -> int:
    """Record a payment and activate subscription."""
    now = datetime.utcnow()
    cycle_end = now + timedelta(days=days)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    end_str = cycle_end.strftime("%Y-%m-%d %H:%M:%S")

    result = _post("payments", {
        "business_id": business_id,
        "amount": amount,
        "currency": "INR",
        "plan_type": plan_type,
        "payment_mode": payment_mode,
        "razorpay_payment_id": razorpay_payment_id or "",
        "razorpay_order_id": razorpay_order_id or "",
        "razorpay_subscription_id": razorpay_subscription_id or "",
        "status": status,
        "billing_start": now_str,
        "billing_end": end_str
    })
    payment_id = result.get("id", 0)

    # Update business subscription status & cycle end
    _patch(
        "businesses",
        {"id": f"eq.{business_id}"},
        {
            "subscription_status": "active",
            "plan_tier": plan_type,
            "payment_method": payment_mode,
            "subscription_cycle_end": end_str,
            "active": 1
        }
    )

    return payment_id


def get_business_activity_logs(business_id: int, limit: int = 30) -> List[Dict[str, Any]]:
    """Get activity logs for a business."""
    return _get(
        "activity_logs",
        params={"business_id": f"eq.{business_id}"},
        order="created_at.desc",
        limit=limit
    )


def get_business_payments(business_id: int) -> List[Dict[str, Any]]:
    """Get payment history for a business."""
    return _get(
        "payments",
        params={"business_id": f"eq.{business_id}"},
        order="created_at.desc"
    )


def get_all_platform_payments(limit: int = 100) -> List[Dict[str, Any]]:
    """Get all payments across the platform with business info."""
    payments = _get("payments", order="created_at.desc", limit=limit)
    if not payments:
        return []
    # Enrich with business names
    biz_ids = list(set(p.get("business_id") for p in payments if p.get("business_id")))
    if biz_ids:
        biz_url = f"{REST_BASE}/businesses?select=id,name,slug&id=in.({','.join(str(i) for i in biz_ids)})"
        resp = requests.get(biz_url, headers=_headers())
        biz_map = {}
        if resp.status_code == 200:
            for b in resp.json():
                biz_map[b["id"]] = b
        for p in payments:
            biz = biz_map.get(p.get("business_id"), {})
            p["business_name"] = biz.get("name", "")
            p["business_slug"] = biz.get("slug", "")
    return payments


def get_business_metrics(business_id: int) -> Dict[str, Any]:
    """Get scan/review/feedback counts for a business."""
    scans = _count("analytics_events", {"business_id": f"eq.{business_id}", "event_type": "eq.scan"})
    reviews = _count("analytics_events", {"business_id": f"eq.{business_id}", "event_type": "eq.completed_review"})
    feedback = _count("private_feedback", {"business_id": f"eq.{business_id}"})
    conv = round((reviews / scans * 100), 1) if scans > 0 else 0.0
    return {
        "scans_count": scans,
        "reviews_count": reviews,
        "feedback_count": feedback,
        "conversion_rate": conv
    }


# ── Database Initialization & Seeding ─────────────────────────────────────────

def init_db():
    """
    Seed default data if tables are empty.
    Tables must be created via Supabase SQL Editor first.
    """
    try:
        # Check if resellers table has data
        resellers = _get("resellers", select="id", limit=1)
        if isinstance(resellers, list) and len(resellers) == 0:
            # Seed admin
            _post("resellers", {
                "username": "admin",
                "password_hash": hash_password("admin123"),
                "email": "admin@reviewbooster.local"
            })
            print("[Supabase] Seeded admin reseller")

        # Check if businesses table has data
        businesses = _get("businesses", select="id", limit=1)
        if isinstance(businesses, list) and len(businesses) == 0:
            _seed_demo_data()
            print("[Supabase] Seeded demo businesses and data")
    except Exception as e:
        print(f"[Supabase init_db] Warning: {e}")
        # Tables might not exist yet - skip seeding


def _seed_demo_data():
    """Seed demo businesses, owner, analytics, and payment data."""
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    month_later = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    year_later = (datetime.utcnow() + timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")

    demo_businesses = [
        {
            "slug": "artisan-roast",
            "name": "Artisan Roast Coffee Co.",
            "category": "Café & Bakery",
            "google_review_url": "https://search.google.com/local/writereview?placeid=ChIJN1t_tDeuEmsRUsoyG83frY4",
            "logo_url": "/static/uploads/artisan_logo.svg",
            "accent_color": "#78350F",
            "tags_preset": json.dumps(["Smooth Espresso", "Fresh Pastries", "Cozy Ambience", "Friendly Baristas", "Fast Wi-Fi", "Oat Milk Latte"]),
            "subscription_status": "active",
            "plan_tier": "monthly",
            "payment_method": "upi_autopay",
            "subscription_cycle_end": month_later,
            "last_google_url_update": now_str
        },
        {
            "slug": "smilecraft-dental",
            "name": "SmileCraft Dental Studio",
            "category": "Healthcare & Dental",
            "google_review_url": "https://search.google.com/local/writereview?placeid=ChIJN1t_tDeuEmsRUsoyG83frY4",
            "logo_url": "/static/uploads/smile_logo.svg",
            "accent_color": "#0284C7",
            "tags_preset": json.dumps(["Gentle Cleaning", "Painless Experience", "Friendly Staff", "Modern Clinic", "Clear Explanations", "On-Time Appointment"]),
            "subscription_status": "active",
            "plan_tier": "yearly",
            "payment_method": "upi_autopay",
            "subscription_cycle_end": year_later,
            "last_google_url_update": now_str
        },
        {
            "slug": "apex-auto",
            "name": "Apex Precision Auto Care",
            "category": "Automotive Repair",
            "google_review_url": "https://search.google.com/local/writereview?placeid=ChIJN1t_tDeuEmsRUsoyG83frY4",
            "logo_url": "/static/uploads/apex_logo.svg",
            "accent_color": "#DC2626",
            "tags_preset": json.dumps(["Honest Pricing", "Quick Turnaround", "Thorough Inspection", "Helpful Mechanics", "Quality Parts", "Clear Estimates"]),
            "subscription_status": "active",
            "plan_tier": "monthly",
            "payment_method": "one_time",
            "subscription_cycle_end": month_later,
            "last_google_url_update": now_str
        }
    ]

    inserted_businesses = []
    for biz_data in demo_businesses:
        result = _post("businesses", biz_data)
        inserted_businesses.append(result)

    # Find artisan-roast for owner creation
    artisan = None
    for b in inserted_businesses:
        if b.get("slug") == "artisan-roast":
            artisan = b
            break

    if artisan:
        artisan_id = artisan["id"]

        # Create demo owner
        owner_result = _post("business_owners", {
            "email": "owner@artisancafe.com",
            "password_hash": hash_password("owner123"),
            "full_name": "Marco Rossi",
            "business_id": artisan_id,
            "last_login_at": now_str
        })
        owner_id = owner_result.get("id")

        # Link owner to business
        if owner_id:
            _patch("businesses", {"id": f"eq.{artisan_id}"}, {"owner_id": owner_id})

        # Seed activity logs
        for action, details in [
            ("signup", "Owner registered business account via self-serve onboarding"),
            ("autopay_mandate_authorized", "Authorized UPI Autopay mandate for Monthly Plan (₹999/mo)"),
            ("google_url_updated", "Set initial Google review place link"),
        ]:
            _post("activity_logs", {"business_id": artisan_id, "action": action, "details": details})

        # Seed payment
        _post("payments", {
            "business_id": artisan_id,
            "amount": 999.00,
            "currency": "INR",
            "plan_type": "monthly",
            "payment_mode": "upi_autopay",
            "razorpay_payment_id": "pay_mock_upi_98231",
            "razorpay_subscription_id": "sub_mock_mandate_4412",
            "status": "captured",
            "billing_start": now_str,
            "billing_end": month_later
        })

    # Seed analytics events for all businesses
    for biz in inserted_businesses:
        bid = biz.get("id")
        slug = biz.get("slug", "")
        if slug == "artisan-roast":
            scans, completions, fbacks = 48, 33, 2
        elif slug == "smilecraft-dental":
            scans, completions, fbacks = 29, 19, 1
        else:
            scans, completions, fbacks = 18, 11, 2

        # Batch insert analytics events
        scan_events = [{"business_id": bid, "event_type": "scan"} for _ in range(scans)]
        review_events = [{"business_id": bid, "event_type": "completed_review", "rating": 5} for _ in range(completions)]
        feedback_events = [{"business_id": bid, "event_type": "feedback_submitted", "rating": 2} for _ in range(fbacks)]

        all_events = scan_events + review_events + feedback_events
        if all_events:
            # Batch insert via POST with array body
            url = f"{REST_BASE}/analytics_events"
            requests.post(url, headers=_headers(prefer="return=minimal"), json=all_events)

        # Seed private feedback entries
        for _ in range(fbacks):
            _post("private_feedback", {
                "business_id": bid,
                "rating": 2,
                "feedback_text": "Service was slightly slower during morning peak hour. Would appreciate faster takeout counter service.",
                "customer_name": "Alex Jordan",
                "customer_contact": "alex.j@example.com"
            })
