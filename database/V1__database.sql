drop table if exists execution;
drop table if exists "user";
drop table if exists issue;
drop table if exists project;
drop table if exists "group";

CREATE TABLE "group"
(
    id_group SERIAL PRIMARY KEY,
    ds_name  VARCHAR(255) NOT NULL
);

CREATE TABLE project
(
    id_project     SERIAL PRIMARY KEY,
    ds_name        VARCHAR(255) NOT NULL,
    ds_branch_name VARCHAR(255) NOT NULL,
    id_group       INT          NOT NULL REFERENCES "group" (id_group),
    lk_repository  VARCHAR(255)
);

CREATE TABLE issue
(
    id_issue      SERIAL PRIMARY KEY,
    tx_issue      TEXT         NOT NULL,
    lk_file       VARCHAR(255) NOT NULL,
    nr_start_line INT,
    nr_end_line   INT,
    tp_issue      VARCHAR(255) NOT NULL,
    id_project    INT          NOT NULL REFERENCES project (id_project) ON DELETE CASCADE
);

create table execution
(
    id_execution SERIAL primary key,
    tp_status    INTEGER   not null,
    dh_request   TIMESTAMP not null default CURRENT_TIMESTAMP,
    dh_started   TIMESTAMP,
    dh_ended     TIMESTAMP,
    qt_attempt   INTEGER   not null default 1,
    ds_detail    text,
    qt_issue     INTEGER,
    id_project   INT       NOT NULL REFERENCES project (id_project) ON DELETE CASCADE
);
