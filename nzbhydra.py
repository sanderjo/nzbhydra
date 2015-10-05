import ssl
from OpenSSL import SSL
import nzbhydra.config as config
from nzbhydra import log
from nzbhydra import providers
from nzbhydra import database

from os.path import dirname
import argparse
import os
import sys


# Root path
base_path = dirname(os.path.abspath(__file__))

# Insert local directories into path
sys.path.insert(0, os.path.join(base_path, 'nzbhydra'))

import requests

requests.packages.urllib3.disable_warnings()

logger = None


def run():
    global logger
    parser = argparse.ArgumentParser(description='Demo')
    parser.add_argument('--config', action='store', help='Settings file to load', default="settings.cfg")
    parser.add_argument('--database', action='store', help='Database file to load', default="nzbhydra.db")
    parser.add_argument('--host', action='store', help='Host to run on', default="127.0.0.1")
    parser.add_argument('--port', action='store', help='Port to run on', default=5050, type=int)
    args = parser.parse_args()

    settings_file = args.config
    database_file = args.database

    print("Loading settings from %s" % settings_file)
    config.load(settings_file)
    logger = log.setup_custom_logger('root')
    logger.info("Started")
    logger.info("Loading database file %s" % database_file)
    database.db.init(database_file)
    database.db.connect()
    providers.read_providers_from_config()
    
    host = config.mainSettings.host.get() if args.host is not None else args.host
    port = config.mainSettings.port.get() if args.port is not None else args.port
    context = None
    if config.mainSettings.ssl.get():
        context = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
        context.load_cert_chain(config.mainSettings.sslcert.get(), config.mainSettings.sslkey.get())
    
    if config.mainSettings.debug.get():
        logger.info("Debug mode enabled")
    logger.info("Starting web app on %s:%d" % (host, port))
    from nzbhydra.web import app
    app.run(host=host, port=port, debug=config.mainSettings.debug.get(), ssl_context=context)


if __name__ == '__main__':
    run()
