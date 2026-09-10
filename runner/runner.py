import argparse
import datetime
import json
import os
import sys
import tempfile
import threading

from shared.database import connect, ensure_schema


def execute():
    parser = argparse.ArgumentParser()
    parser.add_argument("--GROUP_NAME", required=True, help="Nome do grupo")
    parser.add_argument("--PROJECT_NAME", required=True, help="Nome do projeto")
    parser.add_argument("--BRANCH_NAME", required=True, help="Nome da branch")
    parser.add_argument(
        "--SOURCE_PATH", required=True, help="Caminho de um projeto ja clonado"
    )
    parser.add_argument(
        "--PROCESSOR_PATH",
        required=True,
        help="Caminho da pasta automatic-code-review-processor",
    )
    args = parser.parse_args()

    processor_path = os.path.abspath(args.PROCESSOR_PATH)
    if not os.path.isdir(processor_path):
        raise ValueError(f"PROCESSOR_PATH nao e uma pasta valida: {processor_path}")

    source_path = os.path.abspath(args.SOURCE_PATH) if args.SOURCE_PATH else None
    if source_path and not os.path.isdir(source_path):
        raise ValueError(f"SOURCE_PATH nao e uma pasta valida: {source_path}")

    sys.path.insert(0, processor_path)
    from app.processor import review  # pyright: ignore[reportMissingImports]

    started_at = datetime.datetime.now(datetime.timezone.utc)

    output = ""
    status = "failed"
    project_url = source_path
    run_path = None

    try:
        run_path = source_path
        path_source = source_path
        path_resources = os.path.join(processor_path, "resources")
        merge = {
            "author": "",
            "project_name": args.PROJECT_NAME,
            "changes": build_merge_changes(path_source),
            "labels": [],
            "branch": {"target": "", "source": args.BRANCH_NAME},
            "commits_behind": [],
        }
        comments, output, review_error = run_review_with_output(
            path_source,
            path_resources,
            merge,
            run_path,
            review,
        )
        if review_error:
            raise review_error

        save_review(
            comments,
            args.GROUP_NAME,
            args.PROJECT_NAME,
            args.BRANCH_NAME,
            project_url,
        )
        status = "success"
        print(output, end="")
        print(json.dumps(comments))
        print("Issues salvas no PostgreSQL")
        print(f"Workspace preservado em: {run_path}")
    except Exception as error:
        output = f"{output}\n{type(error).__name__}: {error}\n"
        print(output, end="", file=sys.stderr)
        raise
    finally:
        finished_at = datetime.datetime.now(datetime.timezone.utc)
        save_execution(
            group_name=args.GROUP_NAME,
            project_name=args.PROJECT_NAME,
            branch_name=args.BRANCH_NAME,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            output=output,
        )


def run_review_with_output(
    path_source,
    path_resources,
    merge,
    working_directory,
    review,
):
    capture_path = tempfile.mktemp(prefix="acr-dashboard-output-")
    stdout_fd = os.dup(1)
    stderr_fd = os.dup(2)
    pipe_read_fd, pipe_write_fd = os.pipe()
    review_error = None
    comments = []
    original_working_directory = os.getcwd()
    try:
        with open(capture_path, "w+", encoding="utf-8") as capture_file:

            def tee_output():
                while True:
                    chunk = os.read(pipe_read_fd, 8192)
                    if not chunk:
                        break
                    capture_file.write(chunk.decode("utf-8", errors="replace"))
                    capture_file.flush()
                    os.write(stdout_fd, chunk)

            output_thread = threading.Thread(target=tee_output, daemon=True)
            output_thread.start()
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(pipe_write_fd, 1)
            os.dup2(pipe_write_fd, 2)
            try:
                os.chdir(working_directory)
                comments, _ = review.review(
                    path_target="",
                    path_source=path_source,
                    path_source_v2=path_source,
                    path_resources=path_resources,
                    merge=merge,
                    stage="static",
                    config_global={},
                    execution_purpose="source_code_review",
                )
            except (
                OSError,
                ValueError,
                RuntimeError,
                TypeError,
                KeyError,
                AttributeError,
            ) as error:
                review_error = error
            finally:
                os.chdir(original_working_directory)
                sys.stdout.flush()
                sys.stderr.flush()
                os.dup2(stdout_fd, 1)
                os.dup2(stderr_fd, 2)
                os.close(pipe_write_fd)
                output_thread.join()
            capture_file.seek(0)
            output = capture_file.read()
    finally:
        os.close(stdout_fd)
        os.close(stderr_fd)
        os.close(pipe_read_fd)
        os.unlink(capture_path)
    return comments, output, review_error


def build_merge_changes(path_source):
    changes = []
    for root, directories, filenames in os.walk(path_source):
        directories[:] = [directory for directory in directories if directory != ".git"]
        for filename in filenames:
            file_path = os.path.join(root, filename)
            relative_path = os.path.relpath(file_path, path_source)
            changes.append(
                {
                    "new_path": relative_path.replace(os.sep, "/"),
                    "deleted_file": False,
                    "new_file": True,
                }
            )
    return sorted(changes, key=lambda change: change["new_path"])


def save_execution(
    group_name,
    project_name,
    branch_name,
    started_at,
    finished_at,
    status,
    output,
):
    duration_seconds = (finished_at - started_at).total_seconds()
    with connect() as connection:
        ensure_schema(connection)
        connection.execute(
            """
            INSERT INTO executions
                (group_name, project_name, branch_name, started_at, finished_at,
                 duration_seconds, status, output)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                group_name,
                project_name,
                branch_name,
                started_at,
                finished_at,
                duration_seconds,
                status,
                output,
            ),
        )
        connection.commit()


def save_review(comments, group_name, project_name, branch_name, project_url):
    with connect() as connection:
        ensure_schema(connection)
        connection.execute(
            "INSERT INTO groups (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            (group_name,),
        )
        group_id = connection.execute(
            "SELECT id FROM groups WHERE name = %s", (group_name,)
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO projects (group_id, name, clone_url)
            VALUES (%s, %s, %s)
            ON CONFLICT(group_id, name) DO UPDATE SET clone_url = EXCLUDED.clone_url
            """,
            (group_id, project_name, project_url),
        )
        project_id = connection.execute(
            "SELECT id FROM projects WHERE group_id = %s AND name = %s",
            (group_id, project_name),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO branches (project_id, name) VALUES (%s, %s) "
            "ON CONFLICT (project_id, name) DO NOTHING",
            (project_id, branch_name),
        )
        branch_id = connection.execute(
            "SELECT id FROM branches WHERE project_id = %s AND name = %s",
            (project_id, branch_name),
        ).fetchone()[0]
        connection.execute("DELETE FROM issues WHERE branch_id = %s", (branch_id,))
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO issues
                    (branch_id, issue_id, type, comment, position_json, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        branch_id,
                        comment.get("id", ""),
                        comment.get("type", "sem tipo"),
                        comment.get("comment", ""),
                        json.dumps(comment.get("position", {}), ensure_ascii=False),
                        datetime.datetime.now(datetime.timezone.utc),
                    )
                    for comment in comments
                ],
            )
        connection.commit()


if __name__ == "__main__":
    execute()
