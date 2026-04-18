import os

# httpx will be imported lazily inside pull_api

# Optional MariaDB import so modules can import without DB driver present
try:  # pragma: no cover - optional dependency handling
    import mariadb  # type: ignore
except Exception:  # noqa: BLE001
    mariadb = None  # type: ignore

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
        import httpx  # type: ignore

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
                with open(filepath, encoding="utf-8") as f:
                    content = f.read().rstrip("\n")
                return content


async def db_connect(my_logger):
    if mariadb is None:
        my_logger.error(
            "MariaDB driver not available; database disabled in this environment"
        )
        return False
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
    except Exception as e:  # noqa: BLE001
        my_logger.error("Error connecting to MariaDB Platform: %s", e)
        return False


def get_logger(name):
    import logging

    env = load_secret("env")
    my_logger = logging.getLogger(name + "_" + env)
    if my_logger.handlers:
        return my_logger

    my_logger.setLevel(logging.DEBUG)
    my_logger.propagate = False

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(name)s - %(funcName)s - %(lineno)d - %(message)s")
    console_handler.setFormatter(formatter)
    my_logger.addHandler(console_handler)

    return my_logger
