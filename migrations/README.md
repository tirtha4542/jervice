# Local safety migration

`001_local_safety.sql` is an optional migration for the existing local PostgreSQL schema. It adds the active-session/recipe uniqueness indexes and numeric checks used by the updated SQLAlchemy models.

Review and apply it only after taking a local backup:

```powershell
psql $env:DATABASE_URL -f migrations/001_local_safety.sql
```

The application does not run this file automatically. A cloud/backend deployment should use the backend team's migration system instead.
