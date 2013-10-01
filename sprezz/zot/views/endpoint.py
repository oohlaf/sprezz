import json
import logging

from pprint import pformat
from pyramid.view import view_config
from zope.component import ComponentLookupError

from ..endpoint import ZotEndpoint, ZotMagicAuth
from sprezz.interfaces import IPostEndpoint
from sprezz.util.folder import find_service


log = logging.getLogger(__name__)


class ZotEndpointView(object):
    def __init__(self, context, request):
        self.context = context
        self.request = request
        self.graph = request.graph

    @view_config(context=ZotEndpoint,
                 renderer='json')
    def post(self):
        result = {'success': False}
        data = self.request.params.get('data', {})
        log.debug('post: request data = {}'.format(pformat(data)))
        try:
            data = json.loads(data, encoding=self.request.charset)
        except ValueError:
            log.error('post: No valid JSON data received.')
            # We haven't reached the crypto part yet,
            # so it's safe to return here.
            return result

        zot_service = find_service(self.context, 'zot')
        try:
            data = zot_service.aes_decapsulate_json(data)
        except (KeyError, TypeError, ValueError):
            # To prevent Bleichenbacher's attack, don't inform the sender that
            # the received data is malformed and for which reason.
            # It will fail later looking for a post utility.
            log.exception(e)
        log.debug('post: data = {}'.format(pformat(data)))

        # Default to bogus data in case one of the above steps failed.
        post_type = data.get('type', 'bogus').lower()
        post_utility = 'post_{}'.format(post_type)
        try:
            PostUtility = self.request.registry.getUtility(IPostEndpoint,
                                                           post_utility)
        except ComponentLookupError:
            log.error('post: No post endpoint found for type {}.'.format(
                post_type))
            return result
        else:
            post_dispatch = PostUtility(self.context, self.request)
            return post_dispatch.post(data)

    @view_config(context=ZotMagicAuth,
                 renderer='json')
    def magic_auth(self):
        log.debug('magic_auth: request params = {}'.format(
            pformat(self.request.params)))
        return {'project': 'magic auth'}
