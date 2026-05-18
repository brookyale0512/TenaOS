#!/usr/bin/env python3
import os
import re
import zipfile


WAR_PATH = "/opt/openmrs/distribution/openmrs_core/openmrs.war"
TMP_PATH = f"{WAR_PATH}.tmp"
WEB_XML_PATH = "WEB-INF/web.xml"
OAUTH_MAPPINGS = """\t<servlet-mapping>\n \t\t<servlet-name>openmrs</servlet-name>\n \t\t<url-pattern>/oauth2login</url-pattern>\n\t</servlet-mapping>\n\t\n\t<servlet-mapping>\n \t\t<servlet-name>openmrs</servlet-name>\n \t\t<url-pattern>/oauth2logout</url-pattern>\n\t</servlet-mapping>\n"""
WS_MAPPING_RE = re.compile(
    r"(<servlet-mapping>\s*<servlet-name>\s*openmrs\s*</servlet-name>\s*<url-pattern>\s*/ws/\*\s*</url-pattern>\s*</servlet-mapping>)",
    re.MULTILINE,
)


with zipfile.ZipFile(WAR_PATH, "r") as zin, zipfile.ZipFile(TMP_PATH, "w") as zout:
    for item in zin.infolist():
        data = zin.read(item.filename)
        if item.filename == WEB_XML_PATH:
            text = data.decode("utf-8")
            if "/oauth2login" not in text:
                match = WS_MAPPING_RE.search(text)
                if match is None:
                    raise SystemExit("Unable to locate OpenMRS /ws/* servlet mapping in WEB-INF/web.xml")
                text = text[: match.end()] + "\n" + OAUTH_MAPPINGS + text[match.end() :]
            data = text.encode("utf-8")
        zout.writestr(item, data)

os.replace(TMP_PATH, WAR_PATH)
