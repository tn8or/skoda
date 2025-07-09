import os

# paths to look for secrets
SECRET_PATHS = [
    "/etc/secrets/tronity",
    "/etc/secrets/redis",
    "/run/secrets",
    "./secrets",
]


def load_secret(secret):
    if secret in os.environ:
        return os.environ.get(secret)
    else:
        for path in SECRET_PATHS:
            filepath = path + "/" + secret
            if os.path.exists(filepath):
                content = open(filepath).read().rstrip("\n")
                return content
