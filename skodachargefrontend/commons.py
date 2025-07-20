import os

# paths to look for secrets
SECRET_PATHS = [
    "/etc/secrets/tronity",
    "/etc/secrets/redis",
    "/run/secrets",
    "./secrets",
]

SLEEPTIME = 60  # seconds


def load_secret(secret):
    if secret in os.environ:
        return os.environ.get(secret)
    else:
        for path in SECRET_PATHS:
            filepath = path + "/" + secret
            if os.path.exists(filepath):
                content = open(filepath).read().rstrip("\n")
                return content


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
