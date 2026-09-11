"""Human-readable, tenant-scoped matter numbers (SMIT0001).

Adds the prefix and counter that generate a matter number, the number itself on
each matter, and a backfill that numbers every existing matter in creation
order so no matter is left without one.

Assignment lives in ``app/services/matter_number.py`` -- every creation path
calls it, and the counter increment takes a row lock on the tenant, so two
concurrent creations serialize rather than colliding.  This migration adds the
two guarantees that must hold even if a future caller forgets:

* ``uq_matters_tenant_matter_number`` -- a number is never handed to two
  matters in one tenant.
* ``guard_matter_number_immutable`` -- once assigned, a number cannot be
  changed by any statement, including a direct UPDATE.  Only NULL -> value is
  allowed, which is what the backfill and a repair of a pre-existing row need.

Revision ID: 170_matter_number
Revises: 169_automation_services
"""

from alembic import op


revision = "170_matter_number"
down_revision = "169_automation_services"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE tenants ADD COLUMN matter_prefix varchar(4)")
    op.execute(
        "ALTER TABLE tenants ADD CONSTRAINT uq_tenants_matter_prefix "
        "UNIQUE (matter_prefix)"
    )
    op.execute(
        "ALTER TABLE tenants ADD COLUMN matter_sequence_counter integer "
        "NOT NULL DEFAULT 0"
    )
    op.execute("ALTER TABLE matters ADD COLUMN matter_number varchar(20)")
    op.execute("ALTER TABLE matters ADD COLUMN matter_number_seq integer")

    # Derive a prefix for every existing tenant, mirroring
    # app/services/matter_number.derive_tenant_prefix: company name first, then
    # the tenant name, letters only, uppercased, padded to four characters.
    # Collisions resolve by replacing the tail with a counter, so the second
    # "Smith" firm gets SMI2.  The probe checks what has actually been claimed
    # rather than computing a rank -- two different stems sharing a leading
    # substring can otherwise generate the same variant.
    op.execute(
        """
        DO $$
        DECLARE
          t record;
          base text;
          candidate text;
          probe integer;
        BEGIN
          FOR t IN SELECT id, name, company_name FROM tenants ORDER BY created_at, id
          LOOP
            base := rpad(upper(left(COALESCE(
              NULLIF(regexp_replace(COALESCE(t.company_name,''), '[^A-Za-z]', '', 'g'), ''),
              NULLIF(regexp_replace(COALESCE(t.name,''),         '[^A-Za-z]', '', 'g'), ''),
              'MTTR'
            ), 4)), 4, 'X');
            candidate := base;
            probe := 2;
            WHILE EXISTS (SELECT 1 FROM tenants WHERE matter_prefix = candidate) LOOP
              candidate := left(base, 4 - length(probe::text)) || probe::text;
              probe := probe + 1;
              IF probe > 9999 THEN
                candidate := 'MTTR';
                EXIT;
              END IF;
            END LOOP;
            UPDATE tenants SET matter_prefix = candidate WHERE id = t.id;
          END LOOP;
        END $$
        """
    )

    # Number existing matters per tenant in creation order, so the oldest matter
    # is 0001. id breaks ties for rows sharing a created_at.
    op.execute(
        """
        WITH numbered AS (
          SELECT
            m.id,
            t.matter_prefix,
            row_number() OVER (
              PARTITION BY m.tenant_id ORDER BY m.created_at, m.id
            ) AS seq
          FROM matters m
          JOIN tenants t ON t.id = m.tenant_id
        )
        UPDATE matters m
        SET matter_number_seq = n.seq,
            matter_number = n.matter_prefix || CASE
              WHEN n.seq < 10000 THEN lpad(n.seq::text, 4, '0')
              ELSE n.seq::text
            END
        FROM numbered n
        WHERE m.id = n.id
        """
    )

    # The counter is the highest number handed out, so the next matter for a
    # backfilled tenant continues the series rather than restarting at 1.
    op.execute(
        """
        UPDATE tenants t
        SET matter_sequence_counter = COALESCE(
          (SELECT max(m.matter_number_seq) FROM matters m WHERE m.tenant_id = t.id),
          0
        )
        """
    )

    op.execute(
        "ALTER TABLE matters ADD CONSTRAINT uq_matters_tenant_matter_number "
        "UNIQUE (tenant_id, matter_number)"
    )
    op.execute(
        "CREATE INDEX ix_matters_matter_number ON matters (matter_number)"
    )

    op.execute(
        """
        CREATE FUNCTION guard_matter_number_immutable() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
        BEGIN
          IF OLD.matter_number IS NOT NULL
             AND NEW.matter_number IS DISTINCT FROM OLD.matter_number THEN
            RAISE EXCEPTION 'Matter number is immutable once assigned';
          END IF;
          IF OLD.matter_number_seq IS NOT NULL
             AND NEW.matter_number_seq IS DISTINCT FROM OLD.matter_number_seq THEN
            RAISE EXCEPTION 'Matter number sequence is immutable once assigned';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_matter_number_immutable
        BEFORE UPDATE ON matters
        FOR EACH ROW EXECUTE FUNCTION guard_matter_number_immutable()
        """
    )


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_matter_number_immutable ON matters")
    op.execute("DROP FUNCTION IF EXISTS guard_matter_number_immutable()")
    op.execute("DROP INDEX IF EXISTS ix_matters_matter_number")
    op.execute(
        "ALTER TABLE matters DROP CONSTRAINT IF EXISTS "
        "uq_matters_tenant_matter_number"
    )
    op.execute("ALTER TABLE matters DROP COLUMN IF EXISTS matter_number_seq")
    op.execute("ALTER TABLE matters DROP COLUMN IF EXISTS matter_number")
    op.execute(
        "ALTER TABLE tenants DROP CONSTRAINT IF EXISTS uq_tenants_matter_prefix"
    )
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS matter_sequence_counter")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS matter_prefix")
