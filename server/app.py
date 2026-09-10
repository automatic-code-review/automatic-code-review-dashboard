import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


from shared.database import connect, ensure_schema

ROOT_PATH = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(ROOT_PATH, "index.html")


def dashboard_data(execution_limit=10, execution_status="", execution_search=""):
    execution_limit = max(1, min(int(execution_limit), 100))
    with connect() as connection:
        ensure_schema(connection)
        groups = connection.execute(
            "SELECT id, name FROM groups ORDER BY name"
        ).fetchall()
        projects = connection.execute(
            "SELECT id, group_id, name FROM projects ORDER BY name"
        ).fetchall()
        branches = connection.execute(
            "SELECT id, project_id, name FROM branches ORDER BY name"
        ).fetchall()
        issues = connection.execute(
            "SELECT branch_id, issue_id, type, comment, position_json "
            "FROM issues ORDER BY type, issue_id"
        ).fetchall()
        filters = []
        query_parameters = []
        if execution_status in ("success", "failed"):
            filters.append("status = %s")
            query_parameters.append(execution_status)
        if execution_search:
            filters.append(
                "(group_name ILIKE %s OR project_name ILIKE %s OR branch_name ILIKE %s)"
            )
            search_value = f"%{execution_search}%"
            query_parameters.extend([search_value, search_value, search_value])
        status_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        query = f"""
            SELECT id, group_name, project_name, branch_name, started_at,
                     finished_at, duration_seconds, status
            FROM executions
            {status_clause}
            ORDER BY id DESC
                 LIMIT %s
        """
        query_parameters.append(execution_limit)
        executions = connection.execute(query, query_parameters).fetchall()

    issues_by_branch = {}
    for branch_id, issue_id, issue_type, comment, position in issues:
        issues_by_branch.setdefault(branch_id, []).append(
            {
                "id": issue_id,
                "type": issue_type,
                "comment": comment,
                "position": json.loads(position),
            }
        )
    branches_by_project = {}
    for branch_id, project_id, name in branches:
        branches_by_project.setdefault(project_id, []).append(
            {
                "id": branch_id,
                "name": name,
                "issues": issues_by_branch.get(branch_id, []),
            }
        )
    projects_by_group = {}
    for project_id, group_id, name in projects:
        projects_by_group.setdefault(group_id, []).append(
            {
                "id": project_id,
                "name": name,
                "branches": branches_by_project.get(project_id, []),
            }
        )
    return {
        "groups": [
            {
                "id": group_id,
                "name": name,
                "projects": projects_by_group.get(group_id, []),
            }
            for group_id, name in groups
        ],
        "executions": [
            {
                "id": execution[0],
                "group": execution[1],
                "project": execution[2],
                "branch": execution[3],
                "started_at": execution[4].isoformat(),
                "finished_at": execution[5].isoformat(),
                "duration_seconds": execution[6],
                "status": execution[7],
            }
            for execution in executions
        ],
    }


def execution_data(execution_id):
    with connect() as connection:
        ensure_schema(connection)
        execution = connection.execute(
            "SELECT id, group_name, project_name, branch_name, started_at, "
            "finished_at, duration_seconds, status, output "
            "FROM executions WHERE id = %s",
            (execution_id,),
        ).fetchone()
    if execution is None:
        return None
    return {
        "id": execution[0],
        "group": execution[1],
        "project": execution[2],
        "branch": execution[3],
        "started_at": execution[4].isoformat(),
        "finished_at": execution[5].isoformat(),
        "duration_seconds": execution[6],
        "status": execution[7],
        "output": execution[8],
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/health":
            self.send_text("OK")
            return
        if route == "/api/dashboard":
            query = parse_qs(urlparse(self.path).query)
            limit = query.get("limit", [10])[0]
            status = query.get("status", [""])[0]
            search = query.get("search", [""])[0]
            self.send_json(dashboard_data(limit, status, search))
            return
        if route == "/api/executions/latest/output":
            query = parse_qs(urlparse(self.path).query)
            execution = latest_execution(
                query.get("group", [""])[0],
                query.get("project", [""])[0],
                query.get("branch", [""])[0],
            )
            if execution is None:
                self.send_error(404)
                return
            self.send_text(execution)
            return
        if route.startswith("/api/executions/"):
            path_parts = route.strip("/").split("/")
            execution_id = path_parts[2] if len(path_parts) >= 3 else ""
            if not execution_id.isdigit():
                self.send_error(404)
                return
            execution = execution_data(int(execution_id))
            if execution is None:
                self.send_error(404)
                return
            if len(path_parts) == 4 and path_parts[3] == "output":
                self.send_text(execution["output"] or "")
                return
            self.send_json(execution)
            return
        if route == "/":
            self.send_file(HTML_PATH, "text/html; charset=utf-8")
            return
        self.send_error(404)

    def send_json(self, data):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_text(self, text):
        payload = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_file(self, path, content_type):
        with open(path, "rb") as file:
            payload = file.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def latest_execution(group_name, project_name, branch_name):
    with connect() as connection:
        ensure_schema(connection)
        execution = connection.execute(
            "SELECT output FROM executions "
            "WHERE group_name = %s AND project_name = %s AND branch_name = %s "
            "ORDER BY id DESC LIMIT 1",
            (group_name, project_name, branch_name),
        ).fetchone()
    return execution[0] if execution else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--HOST", default="0.0.0.0", help="Interface de rede")
    parser.add_argument("--PORT", type=int, default=8000, help="Porta HTTP")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.HOST, args.PORT), DashboardHandler)
    print(f"Dashboard disponível em http://{args.HOST}:{args.PORT}")
    server.serve_forever()
