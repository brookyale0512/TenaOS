#!/usr/bin/env python3
import os
import zipfile


WAR_PATH = "/opt/openmrs/distribution/openmrs_core/openmrs.war"
TMP_PATH = f"{WAR_PATH}.tmp"


with zipfile.ZipFile(WAR_PATH, "r") as zin, zipfile.ZipFile(TMP_PATH, "w") as zout:
    for item in zin.infolist():
        data = zin.read(item.filename)
        if item.filename == "WEB-INF/classes/log4j2.xml":
            text = data.decode("utf-8")
            text = text.replace("${openmrs:logLayout:-${defaultPattern}}", "${defaultPattern}")
            text = text.replace(
                'fileName="${openmrs:logLocation:-${openmrs:applicationDirectory}}/openmrs.log"',
                'fileName="/opt/openmrs/data/openmrs.log"',
            )
            text = text.replace(
                'filePattern="${openmrs:logLocation:-${openmrs:applicationDirectory}}/openmrs.%i.log"',
                'filePattern="/opt/openmrs/data/openmrs.%i.log"',
            )
            data = text.encode("utf-8")
        zout.writestr(item, data)

os.replace(TMP_PATH, WAR_PATH)
