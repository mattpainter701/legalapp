#!/usr/bin/env python3
"""Seed a representative populated pre-146 baseline before the online upgrade."""

import os
import uuid

import psycopg2
from psycopg2.extras import register_uuid
from sqlalchemy.engine import make_url

register_uuid()

BASELINE_TENANT = uuid.UUID("f0d5c955-7ab8-49c6-95af-8e254f4f1d46")
BASELINE_USER = uuid.UUID("49299810-9841-43da-acaa-c4b0a25e7893")
BASELINE_MATTER = uuid.UUID("23c079f1-73c4-4ce9-b872-7ff9e4d0ec12")


def main():
    parsed = make_url(os.environ["RESEARCH_WORKSPACE_REHEARSAL_DATABASE_URL"])
    with psycopg2.connect(dbname=parsed.database, user=parsed.username, password=parsed.password, host=parsed.host, port=parsed.port) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO tenants (id,name,domain) VALUES (%s,'Pre-146 research baseline','pre146-research.invalid')", (str(BASELINE_TENANT),))
            cur.execute("INSERT INTO users (id,tenant_id,email,full_name) VALUES (%s,%s,'pre146-research@invalid','Pre-146 Research User')", (str(BASELINE_USER), str(BASELINE_TENANT)))
            cur.execute("INSERT INTO matters (id,tenant_id,user_id,slug,matter_name) VALUES (%s,%s,%s,'pre146-research','Pre-146 Research Matter')", (str(BASELINE_MATTER), str(BASELINE_TENANT), str(BASELINE_USER)))
            cur.execute("INSERT INTO matter_assignments (tenant_id,matter_id,user_id,role,is_active_working) VALUES (%s,%s,%s,'associate',true)", (str(BASELINE_TENANT), str(BASELINE_MATTER), str(BASELINE_USER)))
    print("Seeded populated pre-146 research baseline")


if __name__ == "__main__":
    main()
