# SPDX-License-Identifier: Apache-2.0
"""Bounded Qt HTTPS transport for the official DeepSeek API."""

import json


_ENDPOINTS = {
    "https://api.deepseek.com/models": ("GET", 20000),
    "https://api.deepseek.com/chat/completions": ("POST", 90000),
}
_LIMIT = 192 * 1024


class PftDeepSeekHttp:
    def __init__(self, core, network, parent):
        self._core, self._network = core, network
        self._manager = network.QNetworkAccessManager(parent)
        self._timer = core.QTimer(self._manager)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timed_out)
        self._active = None

    def request(self, url, key, payload, completed):
        if self._active is not None or url not in _ENDPOINTS:
            raise ValueError("invalid chat request")
        verb, timeout = _ENDPOINTS[url]
        if (verb == "GET") != (payload is None):
            raise ValueError("invalid chat method")
        network = self._network
        request = network.QNetworkRequest(self._core.QUrl(url))
        request.setRawHeader(b"Authorization", b"Bearer " + key.encode("ascii"))
        request.setRawHeader(b"Accept", b"application/json")
        request.setAttribute(network.QNetworkRequest.RedirectPolicyAttribute, network.QNetworkRequest.ManualRedirectPolicy)
        request.setAttribute(network.QNetworkRequest.CacheLoadControlAttribute, network.QNetworkRequest.AlwaysNetwork)
        request.setAttribute(network.QNetworkRequest.CacheSaveControlAttribute, False)
        request.setAttribute(network.QNetworkRequest.CookieLoadControlAttribute, network.QNetworkRequest.Manual)
        request.setAttribute(network.QNetworkRequest.CookieSaveControlAttribute, network.QNetworkRequest.Manual)
        if verb == "GET":
            reply = self._manager.get(request)
        else:
            request.setRawHeader(b"Content-Type", b"application/json")
            data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            reply = self._manager.post(request, data)
        self._active = (reply, bytearray(), completed)
        reply.setReadBufferSize(_LIMIT + 1)
        reply.readyRead.connect(lambda: self._read(reply))
        reply.finished.connect(lambda: self._finished(reply))
        self._timer.start(timeout)

    def _read(self, reply):
        active = self._active
        if active is None or active[0] is not reply:
            return False
        buffer = active[1]
        buffer.extend(bytes(reply.read(_LIMIT + 1 - len(buffer))))
        if len(buffer) > _LIMIT:
            self._settle(reply, 0, b"", "SIZE", abort=True)
            return False
        return True

    def _finished(self, reply):
        if not self._read(reply):
            return
        network = self._network
        code = reply.attribute(network.QNetworkRequest.HttpStatusCodeAttribute)
        status = code if type(code) is int else 0
        buffer = self._active[1]
        fault = None
        if not status or (status == 200 and reply.error() != network.QNetworkReply.NoError):
            fault = "NETWORK"
        elif status == 200:
            media = reply.header(network.QNetworkRequest.ContentTypeHeader)
            if type(media) is not str or media.split(";", 1)[0].strip().lower() != "application/json":
                fault = "RESPONSE"
        self._settle(reply, status, bytes(buffer) if status == 200 and not fault else b"", fault)

    def _settle(self, reply, status, body, fault, abort=False, notify=True):
        if self._active is None or self._active[0] is not reply:
            return
        completed = self._active[2]
        self._active = None
        self._timer.stop()
        try:
            if abort:
                reply.abort()
        finally:
            reply.deleteLater()
        if notify:
            completed(status, body, fault)

    def _timed_out(self):
        if self._active is not None:
            self._settle(self._active[0], 0, b"", "TIMEOUT", abort=True)

    def cancel(self):
        if self._active is not None:
            self._settle(self._active[0], 0, b"", None, abort=True, notify=False)


def make_deepseek_http(core, parent):
    from PySide import QtNetwork
    return PftDeepSeekHttp(core, QtNetwork, parent)
