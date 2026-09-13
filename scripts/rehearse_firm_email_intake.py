"""Verify firm-alias constraints and real RLS on a migrated disposable database.

Set DATABASE_URL to a PostgreSQL database with 'test' or 'rehearsal' in its name.
All fixtures and the temporary NOLOGIN role are rolled back.
"""
import asyncio
import os
import uuid
from urllib.parse import urlparse

import asyncpg


async def main():
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    database = urlparse(url).path.lower()
    if not any(marker in database for marker in ("test", "rehearsal")):
        raise RuntimeError("Use a disposable test/rehearsal database")
    conn = await asyncpg.connect(url)
    transaction = conn.transaction()
    await transaction.start()
    try:
        first, second = uuid.uuid4(), uuid.uuid4()
        aliases = []
        for tenant in (first, second):
            aliases.append(await conn.fetchval("""INSERT INTO firm_inbound_email_aliases
                (tenant_id,token_hash,encrypted_local_part) VALUES ($1,$2,'fixture') RETURNING id""",
                tenant, uuid.uuid4().hex * 2))
        for statement, error in [
            ("INSERT INTO firm_inbound_email_aliases (tenant_id,token_hash,encrypted_local_part) VALUES ($1,$2,'fixture')", asyncpg.UniqueViolationError),
            ("INSERT INTO inbound_email_aliases (tenant_id,kind,token_hash,encrypted_local_part) VALUES ($1,'matter',$2,'fixture')", asyncpg.NotNullViolationError),
        ]:
            try:
                async with conn.transaction():
                    await conn.execute(statement, first, uuid.uuid4().hex * 2)
            except error:
                pass
            else:
                raise AssertionError("Expected alias constraint rejection")
        for tenant, alias in zip((first, second), aliases):
            await conn.execute("""INSERT INTO inbound_emails
                (tenant_id,firm_alias_id,envelope_sender,recipient,subject,message_sha256,raw_size,occurred_at)
                VALUES ($1,$2,'staff@example.invalid','firm@example.invalid','Private', $3,1,now())""",
                tenant, alias, uuid.uuid4().hex * 2)
        await conn.execute("CREATE ROLE firm_intake_rehearsal NOLOGIN")
        try:
            async with conn.transaction():
                await conn.execute("UPDATE inbound_emails SET status='accepted' WHERE firm_alias_id=$1", aliases[0])
        except asyncpg.CheckViolationError:
            pass
        else:
            raise AssertionError("Accepted correspondence must have a matter")
        await conn.execute("GRANT USAGE ON SCHEMA public TO firm_intake_rehearsal")
        await conn.execute("GRANT SELECT, UPDATE ON firm_inbound_email_aliases, inbound_emails TO firm_intake_rehearsal")
        await conn.execute("SET LOCAL ROLE firm_intake_rehearsal")
        await conn.execute("SELECT set_config('app.current_tenant_id',$1,true)", str(first))
        assert await conn.fetchval("SELECT count(*) FROM inbound_emails WHERE firm_alias_id=ANY($1::uuid[])", aliases) == 1
        assert await conn.fetchval("SELECT count(*) FROM firm_inbound_email_aliases WHERE id=ANY($1::uuid[])", aliases) == 1
        assert await conn.execute("UPDATE inbound_emails SET subject='wrong' WHERE tenant_id=$1", second) == "UPDATE 0"
        await conn.execute("SELECT set_config('app.inbound_email_route_lookup','on',true)")
        assert await conn.fetchval("SELECT count(*) FROM firm_inbound_email_aliases WHERE id=ANY($1::uuid[])", aliases) == 2
        assert await conn.fetchval("SELECT count(*) FROM inbound_emails WHERE firm_alias_id=ANY($1::uuid[])", aliases) == 1
        print("PASS: firm alias uniqueness, matter alias constraint, nullable queue, tenant RLS, read-only routing boundary")
    finally:
        await transaction.rollback()
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
