import json
import logging

from persistent import Persistent
from pprint import pformat
from pyramid.traversal import resource_path
from pyramid.view import view_config

from ..content import content
from ..util.folder import find_service


log = logging.getLogger(__name__)


@content('ZotEndpoint')
class ZotEndpoint(Persistent):
    def get_callback_path(self):
        return resource_path(self)

    def __getitem__(self, key):
        # TODO check if a channel with nick name key exists
        if True:
            dispatch = ZotMagicAuth()
            dispatch.__parent__ = self
            dispatch.__name__ = key
            return dispatch
        raise KeyError(key)


class ZotMagicAuth(object):
    pass


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
            data = zot_service.decapsulate_data(data)
        except KeyError:
            # Data is not AES encapsulated
            pass
        except TypeError as e:
            # Either the private key is None or some other
            # TypeError occured during decryption.
            log.exception(e)
            data = {'type': 'bogus'}
        except ValueError as e:
            log.error('post: Could not decrypt received data.')
            log.exception(e)
            # To prevent Bleichenbacher's attack, don't
            # inform the sender that we received malformed
            # data.
            data = {'type': 'bogus'}
        else:
            try:
                data = json.loads(data.decode('utf-8'), encoding='utf-8')
            except ValueError:
                log.error('post: No valid JSON data received.')
                # To prevent Bleichenbacher's attack, don't
                # inform the sender that we received malformed
                # data. Continue with bogus data.
                data = {'type': 'bogus'}
        log.debug('post: data = {}'.format(pformat(data)))

        result['success'] = True
        log.debug('post: result = {}'.format(pformat(result)))
        return result

    @view_config(context=ZotMagicAuth,
                 renderer='json')
    def magic_auth(self):
        log.debug('magic_auth: request params = {}'.format(
            pformat(self.request.params)))
        return {'project': 'magic auth'}
