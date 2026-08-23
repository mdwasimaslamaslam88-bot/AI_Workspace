from app.core import config as config_module


# Unit and disposable-database tests must not inherit workstation runtimes from
# backend/.env. Real-runtime scripts load that file directly and validate those
# configured services separately.
config_module.settings = config_module.Settings(_env_file=None)
