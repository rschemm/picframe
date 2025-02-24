#!/usr/bin/env python
# -*- coding: utf-8 -*-
import time
import os
import logging
import json
import threading

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer  # py3
    import urllib.parse as urlparse
except ImportError:
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer  # py2
    import urlparse

try:
    from pi_heif import register_heif_opener
except ImportError:
    register_heif_opener = None

EXTENSIONS = [".jpg", ".jpeg", ".png"]
if register_heif_opener is not None:
    EXTENSIONS += [".heif", ".heic"]


def heif_to_jpg(fname):
    try:
        from PIL import Image
        try:
            from pi_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            register_heif_opener = None

        image = Image.open(fname)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        image.save("/dev/shm/temp.jpg")  # default 75% quality
        return "/dev/shm/temp.jpg"
    except Exception:
        logger = logging.getLogger("interface_http.heif_to_jpg")
        logger.warning("Failed attempt to convert %s \n** Have you installed pi_heif? **", fname)
        return ""  # this will not render as a page and will generate error TODO serve specific page with explicit error


class RequestHandler(BaseHTTPRequestHandler):

    def do_GET(self):  # noqa: C901
        try:
            path_split = self.path.split("?")
            page_ok = False
            if len(path_split) == 1:  # i.e. no ? - just serve index.html or image
                if path_split[0] != "/":  # serve static page from html_path...
                    html_page = path_split[0].strip("/")
                else:
                    html_page = "index.html"
                _, extension = os.path.splitext(html_page)
                if html_page == "current_image" or extension.lower() in EXTENSIONS:
                    # NB homeassistant needs to pass url ending in an image extension
                    # in order to trigger streaming whatever is the currently showing image
                    content_type = "image"
                    page = self.server._controller.get_current_path()
                    _, extension = os.path.splitext(page)  # as current_image may be heic
                    if extension.lower() in ('.heic', '.heif'):
                        page = heif_to_jpg(page)
                else:
                    page = os.path.join(self.server._html_path, html_page)
                    content_type = "text/html"
                page = urlparse.unquote(page)
                if os.path.isfile(page):
                    self.send_response(200)
                    self.send_header('Content-type', content_type)
                    # TODO check if html or js - in which case application/javascript
                    # really should filter out attempts to render all other file types (jpg etc?)
                    self.end_headers()
                    with open(page, "rb") as f:
                        page_bytes = f.read()
                        self.wfile.write(page_bytes)
                    self.connection.close()
                    page_ok = True
            else:  # server type request - get or set info
                start_time = time.time()
                message = {}
                self.send_response(200)
                self.server._logger.debug('http request from: ' + self.client_address[0])

                for key, value in dict(urlparse.parse_qsl(path_split[1], True)).items():
                    self.send_header('Content-type', 'text')
                    self.end_headers()
                    if key == "all":
                        for subkey in self.server._setters:
                            message[subkey] = getattr(self.server._controller, subkey)
                    elif key in dir(self.server._controller):
                        if value != "":  # parse_qsl can return empty string for value when just querying
                            lwr_val = value.lower()
                            if lwr_val in ("true", "on", "yes"):  # this only works for simple values *not* json style kwargs # noqa: E501
                                value = True
                            elif lwr_val in ("false", "off", "no"):
                                value = False
                            try:
                                if key in self.server._setters:
                                    setattr(self.server._controller, key, value)
                                else:
                                    value = value.replace("\'", "\"")  # only " permitted in json
                                    # value must be json kwargs
                                    getattr(self.server._controller, key)(**json.loads(value))
                            except Exception as e:
                                message['ERROR'] = 'Excepton:{}>{};'.format(key, e)
                        if key in self.server._setters:  # can get info back from controller TODO
                            message[key] = getattr(self.server._controller, key)

                    self.wfile.write(bytes(json.dumps(message), "utf8"))
                    self.connection.close()
                    page_ok = True

                self.server._logger.info(message)
                self.server._logger.debug("request finished in:  %s seconds" % (time.time() - start_time))
            if not page_ok:
                self.send_response(404)
                self.connection.close()
        except Exception as e:
            self.server._logger.warning(e)
            self.send_response(400)
            self.connection.close()

        return

    def log_request(self, code):
        pass

    def do_POST(self):
        self.do_GET()

    def end_headers(self):
        try:
            super().end_headers()
        except BrokenPipeError as e:
            self.connection.close()
            self.server._logger.error('httpserver error: {}'.format(e))


class InterfaceHttp(HTTPServer):
    def __init__(self, controller, html_path, pic_dir, no_files_img, port=9000):
        super(InterfaceHttp, self).__init__(("0.0.0.0", port), RequestHandler)
        # NB name mangling throws a spanner in the works here!!!!!
        # *no* __dunders
        self._logger = logging.getLogger("simple_server.InterfaceHttp")
        self._logger.info("creating an instance of InterfaceHttp")
        self._controller = controller
        self._pic_dir = os.path.expanduser(pic_dir)
        self._no_files_img = os.path.expanduser(no_files_img)
        self._html_path = os.path.expanduser(html_path)
        # TODO check below works with all decorated methods.. seems to work
        controller_class = controller.__class__
        self._setters = [method for method in dir(controller_class)
                         if 'setter' in dir(getattr(controller_class, method))]
        t = threading.Thread(target=self.serve_forever)
        t.start()

    def stop(self):
        self.shutdown()
