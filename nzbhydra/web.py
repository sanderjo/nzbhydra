from functools import wraps
import json
import logging
from pprint import pprint

from flask import send_file, redirect
from flask import Flask, render_template, request, jsonify, Response
import requests
from webargs import Arg
from webargs.flaskparser import use_args
from werkzeug.exceptions import Unauthorized

from nzbhydra.api import process_for_internal_api, get_nfo, process_for_external_api, get_nzb_link, get_nzb_response, download_nzb_and_log
from nzbhydra import config, search, infos
from nzbhydra.config import MainSettings, DownloaderSettings, NzbAccessTypeSelection, NzbAddingTypeSelection
from nzbhydra.downloader import Nzbget

app = Flask(__name__)

logger = logging.getLogger('root')

externalapi_args = {
    "input": Arg(str),
    "apikey": Arg(str),
    "t": Arg(str),
    "q": Arg(str),
    "query": Arg(str),
    "group": Arg(str),
    "limit": Arg(int),
    "offset": Arg(str),
    "cat": Arg(str),
    "o": Arg(str),
    "attrs": Arg(str),
    "extended": Arg(bool),
    "del": Arg(str),
    "rid": Arg(str),
    "genre": Arg(str),
    "imdbid": Arg(str),
    "tvdbid": Arg(str),
    "season": Arg(str),
    "ep": Arg(str)
}

internalapi_args = {
    "apikey": Arg(str),
    "t": Arg(str),
    "query": Arg(str),
    "category": Arg(str),
    "title": Arg(str),
    "rid": Arg(str),
    "imdbid": Arg(str),
    "tvdbid": Arg(str),
    "season": Arg(str),
    "episode": Arg(str),

    "minsize": Arg(int),
    "maxsize": Arg(int),
    "minage": Arg(int),
    "maxage": Arg(int),

    "input": Arg(str),
    "guid": Arg(str),
    "provider": Arg(str),
    "searchid": Arg(str),
    
    "link": Arg(str),
    "downloader" : Arg(str),

}

from webargs import core

parser = core.Parser()


class CustomError(Exception):
    pass


@parser.error_handler
def handle_error(error):
    print(error)
    raise CustomError(error)





def check_auth(username, password):
    """This function is called to check if a username /
    password combination is valid.
    """
    return username == config.get(MainSettings.username) and password == config.get(MainSettings.password)


def authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response(
        'Could not verify your access level for that URL. You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if config.get(MainSettings.enable_auth):
            auth = request.authorization
            if not auth or not check_auth(auth.username, auth.password):
                return authenticate()
        return f(*args, **kwargs)

    return decorated


@app.route('/<path:path>')
@app.route('/', defaults={"path": None})
@requires_auth
def base(path):
    return send_file("static/index.html")


def render_search_results_for_api(search_results):
    return render_template("api.html", channel={}, items=search_results)


@app.route('/api')
@use_args(externalapi_args)
def api(args):
    print(args)
    print(request.url)
    # Map newznab api parameters to internal
    if args["q"] is not None:
        args["query"] = args["q"]  # Because internally we work with "query" instead of "q"
    # todo: category mapping, completely forgot that
    if config.get(Apikey, None) is not None and ("apikey" not in args or args["apikey"] != config.get(MainSettings.apikey)):
        raise Unauthorized("API key not provided or invalid")
    elif args["t"] == "search":
        results = search.search(False, args)
    elif args["t"] == "tvsearch":
        results = search.search_show(False, args)
    else:
        pprint(request)
        return "hello api"
    results = process_for_external_api(results)
    return render_search_results_for_api(results)


@app.route('/internalapi')
@requires_auth
@use_args(internalapi_args, locations=['querystring'])
def internal_api(args):
    results = None
    
    if args["t"] in ("search", "tvsearch", "moviesearch"):
        if args["t"] == "search":
            results = search.search(True, args)
        if args["t"] == "tvsearch":
            results = search.search_show(True, args)
        if args["t"] == "moviesearch":
            results = search.search_movie(True, args)
    
        if results is not None:
            results = process_for_internal_api(results)
            return jsonify(results)  # Flask cannot return lists
        else:
            return "No results", 500
    if args["t"] == "autocompletemovie":
        results = infos.find_movie_ids(args["input"])
        return jsonify({"results": results})
    if args["t"] == "autocompleteseries":
        results = infos.find_series_ids(args["input"])
        return jsonify({"results": results})
    if args["t"] == "categories":
        return jsonify(search.categories)
    if args["t"] == "getnfo":
        nfo = get_nfo(args["provider"], args["guid"])
        return jsonify(nfo)
    if args["t"] == "getnzb": #Returns an NZB. This will probably be only called (internally) if the user wants to download an NZB instead of adding it to the downloader
        if config.isSettingSelection(DownloaderSettings.nzbaccesstype, NzbAccessTypeSelection.redirect):  # I'd like to have this in api but don't want to have to use redirect() there...
            link = get_nzb_link(args["provider"], args["guid"], args["title"], args["searchid"])
            if link is not None:
                return redirect(link)
            else:
                return "Unable to build link to NZB", 404
        elif config.isSettingSelection(DownloaderSettings.nzbaccesstype, NzbAccessTypeSelection.serve):
            return get_nzb_response(args["provider"], args["guid"], args["title"], args["searchid"])
        else:
            logger.error("Invalid value of %s: %s" % (DownloaderSettings.nzbaccesstype.name, config.get(DownloaderSettings.nzbaccesstype)))
            return "downloader.add_type has wrong value", 500 #"direct" would never end up here, so it must be a wrong value
    if args["t"] == "addnzb":
        #todo read config
        downloader = Nzbget()
        if config.get(DownloaderSettings.nzbAddingType) == DownloaderSettings.NzbAddingTypeOptions.link.value: #We send a link to the downloader. The link is either to us (where it gets answered or redirected, thet later getnzb will be called) or directly to the provider
            link = get_nzb_link(args["provider"], args["guid"], args["title"], args["searchid"])
            added = downloader.add_link(link, args["title"], args["category"])
            if added:
                return "Success"
            else:
                return "Error", 500
        elif config.isSettingSelection(DownloaderSettings.nzbAddingType, NzbAddingTypeSelection.nzb): #We download an NZB send it to the downloader
            nzbdownloadresult = download_nzb_and_log(args["provider"], args["guid"], args["title"], args["searchid"])
            added = downloader.add_nzb(nzbdownloadresult.content, args["title"], args["category"])
            if added:
                return "Success"
            else:
                return "Error", 500
        else:
            logger.error("Invalid value of %s: %s" % (DownloaderSettings.nzbAddingType.name, config.get(DownloaderSettings.nzbAddingType)))
            return "downloader.add_type has wrong value", 500 #"direct" would never end up here, so it must be a wrong value
            
            
        
        

    
    return "hello internal api", 500
