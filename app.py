import os

import psycopg2
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY')


def get_db_connection():
    db_host = os.environ.get('DB_HOST')
    db_name = os.environ.get('DB_NAME')
    db_user = os.environ.get('DB_USER')
    db_port = os.environ.get('DB_PORT')
    db_password = os.environ.get('DB_PASSWORD')

    conn = psycopg2.connect(
        host=db_host,
        dbname=db_name,
        user=db_user,
        password=db_password,
        port=db_port
    )

    return conn


@app.route('/group', methods=['GET', 'POST'])
def manage_group():
    if request.method == 'POST':
        group_name = request.form['name']

        if group_name:
            conn = get_db_connection()
            cur = conn.cursor()

            try:
                cur.execute('INSERT INTO "group" (ds_name) VALUES (%s)', (group_name,))
                conn.commit()
                flash('Grupo adicionado com sucesso!', 'success')
            except psycopg2.IntegrityError:
                conn.rollback()
                flash('Erro ao adicionar grupo. Talvez o nome já exista.', 'danger')

            cur.close()
            conn.close()

            return redirect(url_for('list_groups'))  # Redireciona para a listagem de grupos

    return render_template('add_group.html')


@app.route('/group/list')
def list_groups():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute('SELECT id_group, ds_name FROM "group"')
    groups = cur.fetchall()

    cur.close()
    conn.close()

    return render_template('list_groups.html', groups=groups)


@app.route('/projects')
def list_projects():
    group_id = request.args.get('group_id')
    if not group_id:
        flash('ID do grupo é necessário para listar projetos.', 'danger')
        return redirect(url_for('list_groups'))

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id_project, ds_name, ds_branch_name FROM project WHERE id_group = %s", (group_id,))
    projects = cur.fetchall()

    cur.close()
    conn.close()

    return render_template('list_projects.html', projects=projects, group_id=group_id)


@app.route('/projects/add', methods=['GET', 'POST'])
def add_project():
    group_id = request.args.get('group_id')
    if not group_id:
        flash('ID do grupo é necessário para cadastrar um projeto.', 'danger')
        return redirect(url_for('list_groups'))

    if request.method == 'POST':
        project_name = request.form['name']
        branch_name = request.form['branch']
        repositorio_url = request.form['repositorio']

        if project_name and repositorio_url:
            conn = get_db_connection()
            cur = conn.cursor()

            try:
                cur.execute(
                    "INSERT INTO project (ds_name, id_group, ds_branch_name, lk_repository) VALUES (%s, %s, %s, %s)",
                    (project_name, group_id, branch_name, repositorio_url)
                )
                conn.commit()
                flash('Projeto adicionado com sucesso!', 'success')
                return redirect(url_for('list_projects', group_id=group_id))
            except psycopg2.IntegrityError:
                conn.rollback()
                flash('Erro ao adicionar projeto.', 'danger')
            finally:
                cur.close()
                conn.close()

    return render_template('add_project.html', group_id=group_id)


@app.route('/execution/run', methods=['POST'])
def run_execution():
    project_id = request.form.get('project_id')
    group_id = request.form.get('group_id')

    if not group_id:
        flash('ID do grupo não fornecido.', 'danger')
        return redirect(url_for('list_groups'))

    if project_id:
        conn = get_db_connection()
        cur = conn.cursor()

        try:
            # TODO INSERIR APENAS SE NAO TIVER UM PENDENTE PARA ESSE MESMO PROJETO...
            cur.execute(
                "INSERT INTO execution (tp_status, id_project, ds_detail) VALUES (0, %s, 'Na fila de processamento...')",
                (project_id,)
            )
            conn.commit()
            flash(f'Execução do projeto {project_id} registrada como PENDENTE!', 'success')
        except Exception as e:
            conn.rollback()
            flash(f'Erro ao registrar execução: {str(e)}', 'danger')
        finally:
            cur.close()
            conn.close()

        return redirect(url_for('list_executions'))

    flash('ID do projeto não fornecido.', 'danger')
    return redirect(url_for('list_projects', group_id=group_id))


@app.route('/executions')
def list_executions():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT e.id_execution, e.dh_request, e.qt_attempt, e.tp_status, e.dh_started, e.dh_ended, p.ds_name, e.qt_issue
        FROM execution e
        JOIN project p ON e.id_project = p.id_project
    """)

    executions = cur.fetchall()
    cur.close()
    conn.close()

    return render_template('list_executions.html', executions=executions)


@app.route('/execution/details/<int:execution_id>')
def view_execution_details(execution_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT e.dh_request, e.qt_attempt, e.tp_status, e.dh_started, e.dh_ended, p.ds_name, e.ds_detail, e.id_execution
        FROM execution e
        JOIN project p ON e.id_project = p.id_project
        WHERE e.id_execution = %s
    """, (execution_id,))

    execution = cur.fetchone()
    cur.close()
    conn.close()

    if not execution:
        flash(f'Execução {execution_id} não encontrada.', 'danger')
        return redirect(url_for('list_executions'))

    return render_template('execution_details.html', execution=execution)


@app.route('/execution/reprocess/<int:execution_id>', methods=['POST'])
def reprocess_execution(execution_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE execution
        SET tp_status = 0, ds_detail = 'Reprocessando...'
        WHERE id_execution = %s AND tp_status IN (1, 3)
    """, (execution_id,))

    conn.commit()
    cur.close()
    conn.close()

    flash('Execução reprocessada com sucesso.', 'success')
    return redirect(url_for('list_executions'))


@app.route('/execution/cancel/<int:execution_id>', methods=['POST'])
def cancel_execution(execution_id):
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            "UPDATE execution SET tp_status = 3, ds_detail = 'Processo cancelado' WHERE id_execution = %s",
            (execution_id,)
        )
        conn.commit()
        flash(f'Execução {execution_id} cancelada com sucesso!', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Erro ao cancelar execução: {str(e)}', 'danger')
    finally:
        cur.close()
        conn.close()

    return redirect(url_for('list_executions'))


@app.route('/project/<int:project_id>/group/<int:group_id>/issues', methods=['GET', 'POST'])
def list_issues(project_id, group_id):
    tp_issue = request.args.get('tp_issue')

    conn = get_db_connection()
    cur = conn.cursor()

    if tp_issue:
        cur.execute("""
            SELECT tx_issue, lk_file, nr_start_line, nr_end_line, tp_issue
            FROM issue
            WHERE id_project = %s AND tp_issue = %s
        """, (project_id, tp_issue))
    else:
        cur.execute("""
            SELECT tx_issue, lk_file, nr_start_line, nr_end_line, tp_issue
            FROM issue
            WHERE id_project = %s
        """, (project_id,))

    issues = cur.fetchall()
    cur.close()
    conn.close()

    return render_template('issue_list.html', issues=issues, project_id=project_id, group_id=group_id,
                           tp_issue=tp_issue)


@app.route('/')
def index():
    return redirect(url_for('list_groups'))


if __name__ == '__main__':
    app.run(debug=True)
