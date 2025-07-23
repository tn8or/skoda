import os

import httpx
import mariadb

# paths to look for secrets
SECRET_PATHS = [
    "/etc/secrets/tronity",
    "/etc/secrets/redis",
    "/run/secrets",
    "./secrets",
]

SLEEPTIME = 600  # seconds

CHARGEFINDER_URL = "http://chargefinder/find-charges"
CHARGECOLLECTOR_URL = "http://chargecollector/collect-charges"
UPDATECHARGES_URL = "http://skodaupdatechargeprices/update-charges"


async def pull_api(url, my_logger):
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except httpx.RequestError as e:
        my_logger.error(f"Request error: {e}")
    except httpx.HTTPStatusError as e:
        my_logger.error(f"HTTP error: {e}")
    except Exception as e:
        my_logger.error(f"An unexpected error occurred: {e}")
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
        my_logger.error(f"Error connecting to MariaDB Platform: {e}")
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

    # Optional: set a formatter
    formatter = logging.Formatter("%(name)s - %(funcName)s - %(lineno)d - %(message)s")
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    graylog_handler.setFormatter(formatter)

    # Add the handler to the logger
    my_logger.addHandler(file_handler)
    my_logger.addHandler(console_handler)
    my_logger.addHandler(graylog_handler)

    return my_logger
