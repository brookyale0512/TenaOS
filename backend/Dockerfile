# TenaOS Backend — OpenMRS-only runtime

ARG TENAOS_OMRS_SOURCE_IMAGE=openmrs/openmrs-reference-application-3-backend:demo@sha256:88099af8a3461cf4f3b4978e45e56f36974c50913ed1cf9f13f42c60970f1490
ARG TENAOS_FINAL_BASE_IMAGE=ubuntu:24.04@sha256:186072bba1b2f436cbb91ef2567abca677337cfc786c86e107d25b7072feef0c

FROM ${TENAOS_OMRS_SOURCE_IMAGE} AS omrs-src

FROM ${TENAOS_FINAL_BASE_IMAGE}

ARG TENAOS_MYSQL_CONNECTOR_J_URL=https://repo1.maven.org/maven2/com/mysql/mysql-connector-j/8.0.33/mysql-connector-j-8.0.33.jar
ARG TENAOS_MYSQL_CONNECTOR_J_SHA256=e2a3b2fc726a1ac64e998585db86b30fa8bf3f706195b78bb77c5f99bf877bd9

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC \
    JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64

RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jdk-headless \
    mariadb-server \
    supervisor curl ca-certificates tzdata python3 unzip \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system tenaos && \
    useradd --system --gid tenaos --create-home --home-dir /home/tenaos --shell /usr/sbin/nologin tenaos

COPY --from=omrs-src /usr/local/tomcat /opt/tomcat-openmrs
RUN rm -rf /opt/tomcat-openmrs/webapps/*

RUN mkdir -p /opt/openmrs/distribution /opt/openmrs/data
COPY --from=omrs-src /openmrs/distribution/   /opt/openmrs/distribution/
COPY --from=omrs-src /openmrs/startup-init.sh /opt/openmrs/startup-init.sh
COPY --from=omrs-src /openmrs/startup.sh      /opt/openmrs/startup.sh
COPY --from=omrs-src /openmrs/wait-for-it.sh  /opt/openmrs/wait-for-it.sh
COPY build/patch-openmrs-log4j.py /tmp/patch-openmrs-log4j.py
COPY build/patch-openmrs-webservices.py /tmp/patch-openmrs-webservices.py
RUN if [ -f /opt/openmrs/distribution/openmrs_config/addresshierarchy/referenceapplication-demo/addressConfiguration.xml ]; then \
      cp /opt/openmrs/distribution/openmrs_config/addresshierarchy/referenceapplication-demo/addressConfiguration.xml \
         /opt/openmrs/distribution/openmrs_config/addresshierarchy/addressConfiguration.xml; \
    fi && \
    if [ -f /opt/openmrs/distribution/openmrs_config/addresshierarchy/referenceapplication-demo/addresshierarchy.csv ]; then \
      cp /opt/openmrs/distribution/openmrs_config/addresshierarchy/referenceapplication-demo/addresshierarchy.csv \
         /opt/openmrs/distribution/openmrs_config/addresshierarchy/addresshierarchy.csv; \
    fi && \
    python3 /tmp/patch-openmrs-log4j.py && \
    python3 /tmp/patch-openmrs-webservices.py && \
    rm -f /tmp/patch-openmrs-log4j.py /tmp/patch-openmrs-webservices.py

RUN chmod +x /opt/openmrs/startup-init.sh /opt/openmrs/startup.sh /opt/openmrs/wait-for-it.sh && \
    ln -sfn /opt/tomcat-openmrs /usr/local/tomcat && \
    ln -sfn /opt/openmrs /openmrs

RUN curl -fsSL "$TENAOS_MYSQL_CONNECTOR_J_URL" -o /tmp/mysql-connector-j.jar && \
    echo "${TENAOS_MYSQL_CONNECTOR_J_SHA256}  /tmp/mysql-connector-j.jar" | sha256sum -c - && \
    mv /tmp/mysql-connector-j.jar /opt/tomcat-openmrs/lib/mysql-connector-j.jar

COPY scripts/ /opt/tenaos/scripts/
COPY configs/ /opt/tenaos/configs/
RUN chmod +x /opt/tenaos/scripts/*.sh /opt/tenaos/scripts/lib/*.sh && \
    mkdir -p /etc/supervisor/conf.d /opt/openmrs/data /opt/tenaos/data/emr-os/openmrs-managed-config \
      /run/mysqld /var/lib/mysql /var/log/supervisor /var/log/openmrs /target && \
    ln -sf /opt/tenaos/configs/supervisor/supervisord.conf /etc/supervisor/supervisord.conf && \
    chown mysql:mysql /run/mysqld /var/lib/mysql && \
    chown -R tenaos:tenaos /opt/openmrs /opt/tomcat-openmrs /opt/tenaos/data /var/log/openmrs /target

EXPOSE 8080
VOLUME ["/opt/openmrs/data", "/var/lib/mysql", "/opt/tenaos/data/emr-os/openmrs-managed-config"]
ENTRYPOINT ["/opt/tenaos/scripts/entrypoint.sh"]
