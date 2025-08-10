import os
import httpx
import mariadb

SECRET_PATHS = [
    "/etc/secrets/tronity",
    "/etc/secrets/redis",
    "/run/secrets",
    "./secrets",
]
SLEEPTIME = 1800
CHARGEFINDER_URL = "http://chargefinder/find-charges"
CHARGECOLLECTOR_URL = "http://chargecollector/collect-charges"
UPDATECHARGES_URL = "http://skodaupdatechargeprices/update-charges"
UPDATEALLCHARGES_URL = "http://skodaupdatechargeprices/update-all-charges"


async def pull_api(url, my_logger):
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            response.raise_for_status()
            # Try JSON first; fall back to text for plain-text endpoints
            try:
                return response.json()
            except ValueError:
                text = response.text
                my_logger.debug(
                    "pull_api: Non-JSON response from %s (len=%d), returning text",
                    url,
                    len(text) if text is not None else 0,
                )
                return text
    except httpx.RequestError as e:
        my_logger.error("Request error: %s", e)
    except httpx.HTTPStatusError as e:
        my_logger.error("HTTP error: %s", e)
    except Exception as e:
        my_logger.error("An unexpected error occurred: %s", e)
    return None


def load_secret(secret):
    if secret in os.environ:
        return os.environ.get(secret)
    else:
        for path in SECRET_PATHS:
            filepath = path + "/" + secret
            if os.path.exists(filepath):
                content = open(filepath).read().rstrip("\n")
                return content


async def db_connect(my_logger):
    try:
        my_logger.debug("Connecting to MariaDB...")
        conn = mariadb.connect(
            user=load_secret("MARIADB_USERNAME"),
            password=load_secret("MARIADB_PASSWORD"),
            host=load_secret("MARIADB_HOSTNAME"),
            port=3306,
            database=load_secret("MARIADB_DATABASE"),
        )
        conn.auto_reconnect = True
        my_logger.debug("Connected to MariaDB")
        cur = conn.cursor()
        return conn, cur
    except mariadb.Error as e:
        my_logger.error("Error connecting to MariaDB Platform: %s", e)
        return False


def get_logger(name):
    import logging
    import graypy

    env = load_secret("env")
    graylog_host = load_secret("GRAYLOG_HOST")
    graylog_port = load_secret("GRAYLOG_PORT")
    my_logger = logging.getLogger(name + "_" + env)
    my_logger.setLevel(logging.DEBUG)
    file_handler = logging.FileHandler("app.log")
    file_handler.setLevel(logging.DEBUG)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    graylog_handler = graypy.GELFTCPHandler(graylog_host, graylog_port)
    formatter = logging.Formatter("%(name)s - %(funcName)s - %(lineno)d - %(message)s")
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    graylog_handler.setFormatter(formatter)
    my_logger.addHandler(file_handler)
    my_logger.addHandler(console_handler)
    my_logger.addHandler(graylog_handler)
    return my_logger
