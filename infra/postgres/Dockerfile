FROM postgis/postgis:16-3.4-alpine

# Copy initialization scripts into the image so deployment does not depend on
# host bind-mount permissions (notably NAS/ACL-managed directories).
COPY --chmod=644 init/001_extensions.sql /docker-entrypoint-initdb.d/001_extensions.sql
