"""将人工分类归属从一人一类扩展为一人多标签。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


def apply_creator_classification_multilabel_v1(engine: Engine) -> None:
    """把 assignment 主键扩展为 ``market + identity + classification``。

    这是一次结构性、前向迁移：旧表中每个身份最多一行，故不会丢数据；迁移后
    允许新增并存标签。项目数据库固定为 PostgreSQL，非 PostgreSQL 仅由
    ``metadata.create_all`` 在全新测试库中提供正确主键，不执行旧表重建。

    回滚说明：已写入多个标签后不能无损退回单标签主键；如需回滚，必须先做
    备份并由人工定义保留哪一个标签，再通过新的前向迁移收敛数据。
    """
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        connection.execute(text("""
            do $$
            declare
                primary_key_name text;
                primary_key_columns text;
            begin
                select tc.constraint_name
                  into primary_key_name
                  from information_schema.table_constraints tc
                 where tc.table_schema = current_schema()
                   and tc.table_name = 'creator_classification_assignment'
                   and tc.constraint_type = 'PRIMARY KEY';

                if primary_key_name is null then
                    alter table creator_classification_assignment
                        add constraint creator_classification_assignment_pkey
                        primary key (market, identity_key, classification_id);
                    return;
                end if;

                select string_agg(kcu.column_name, ',' order by kcu.ordinal_position)
                  into primary_key_columns
                  from information_schema.key_column_usage kcu
                 where kcu.table_schema = current_schema()
                   and kcu.table_name = 'creator_classification_assignment'
                   and kcu.constraint_name = primary_key_name;

                if primary_key_columns <> 'market,identity_key,classification_id' then
                    execute format(
                        'alter table creator_classification_assignment drop constraint %I',
                        primary_key_name
                    );
                    alter table creator_classification_assignment
                        add constraint creator_classification_assignment_pkey
                        primary key (market, identity_key, classification_id);
                end if;
            end $$;
        """))


__all__ = ["apply_creator_classification_multilabel_v1"]

