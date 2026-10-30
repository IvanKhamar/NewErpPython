"""SQLAlchemy models of the existing tables (database-first: they must match db/*.sql).

Like the EF entities in .NET, a model maps only the columns the code needs; the other columns keep their
database defaults on insert.
"""
