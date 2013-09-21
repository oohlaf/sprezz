import logging
import random
import requests
import sys
import whirlpool

from pyramid.threadlocal import get_current_registry
from pyramid.traversal import find_root
from urllib.parse import urlparse, urlunparse

from ..content import service
from ..folder import Folder
from ..util.base64 import base64_url_encode, base64_url_decode
from ..util.crypto import PersistentRSAKey


log = logging.getLogger(__name__)


@service('Zot', service_name='zot', after_create='after_create')
class Zot(Folder):
    def after_create(self, inst, registry):
        log.info('Generating RSA keys for this site')
        self._private_site_key = PersistentRSAKey()
        self._private_site_key.generate_keypair()
        self._public_site_key = self._private_site_key.get_public_key()

        log.info('Registering Zot services')
        site_service = registry.content.create('ZotSites')
        hub_service = registry.content.create('ZotHubs')
        channel_service = registry.content.create('ZotChannels')
        xchannel_service = registry.content.create('ZotXChannels')
        endpoint_service = registry.content.create('ZotEndpoint')
        poco_service = registry.content.create('ZotPoco')

        self.add('site', site_service, registry=registry)
        self.add('hub', hub_service, registry=registry)
        self.add('channel', channel_service, registry=registry)
        self.add('xchannel', xchannel_service, registry=registry)
        self.add('post', endpoint_service, registry=registry)
        self.add('poco', poco_service, registry=registry)

    @property
    def public_site_key(self):
        return self._public_site_key

    @property
    def site_url(self):
        try:
            return self._v_site_url
        except AttributeError:
            root = find_root(self)
            self._v_site_url = root.app_url
            return root.app_url

    def add_channel(self, nickname, name, *arg, **kw):
        registry = kw.pop('registry', None)
        if registry is None:
            registry = get_current_registry()

        prv_key = PersistentRSAKey()
        prv_key.generate_keypair()
        pub_key = prv_key.get_public_key()

        guid = self._create_channel_guid(nickname)
        signature = self._create_channel_signature(guid, prv_key)
        channel_hash = self._create_channel_hash(guid, signature)

        xchannel = registry.content.create('ZotLocalXChannel',
                                           nickname=nickname,
                                           name=name,  # TODO move to graph?
                                           channel_hash=channel_hash,
                                           guid=guid,
                                           signature=signature,
                                           key=pub_key,
                                           photo=None,
                                           flags=None,
                                           *arg, **kw)
        self['xchannel'].add(channel_hash, xchannel)

        channel = registry.content.create('ZotLocalChannel',
                                          nickname=nickname,
                                          name=name,  # TODO move to graph?
                                          channel_hash=channel_hash,
                                          guid=guid,  # TODO redundant with xchannel?
                                          signature=signature,  # TODO redundant with xchannel?
                                          key=prv_key,
                                          # TODO add various channel flags
                                          *arg, **kw)
        self['channel'].add(nickname, channel)

        hub = registry.content.create('ZotLocalHub',
                                      channel_hash=channel_hash,
                                      guid=guid,
                                      signature=signature,
                                      key=self.public_site_key,
                                      *arg, **kw)
        self['hub'].add(channel_hash, hub)

        log.debug('guid = %s' % guid)
        log.debug('sig = %s' % signature)
        log.debug('sig_hash = %s' % channel_hash)
        log.debug('address = %s' % xchannel.address)
        log.debug('url = %s' % xchannel.url)
        log.debug('connections url = %s' % xchannel.connections_url)
        log.debug('hub url = %s' % hub.url)
        log.debug('hub url sig = %s' % hub.url_signature)
        log.debug('callback = %s' % hub.callback)
        return channel

    def _create_channel_guid(self, nickname, root=None):
        if root is None:
            root = find_root(self)
        uid = '{0}/{1}.{2}'.format(root.app_url, nickname,
                                   random.randint(0, sys.maxsize)
                                   ).encode('utf-8')
        wp = whirlpool.new(uid)
        return base64_url_encode(wp.digest())

    def _create_channel_signature(self, guid, key):
        return base64_url_encode(key.sign_message(guid))

    def _create_channel_hash(self, guid, signature):
        """Create base64 encoded channel hash.

        Arguments 'guid' and 'signature' must be base64 encoded.
        """
        message = '{0}{1}'.format(guid, signature).encode('ascii')
        wp = whirlpool.new(message)
        return base64_url_encode(wp.digest())

    def import_xchannel(self, info, *arg, **kw):
        registry = kw.pop('registry', None)
        if registry is None:
            registry = get_current_registry()

        channel_hash = self._create_channel_hash(info['guid'],
                                                 info['guid_sig'])
        log.debug('import xchan: channel_hash = {}'.format(channel_hash))

        pub_key = PersistentRSAKey(extern_public_key=info['key'])
        if not pub_key.verify_message(info['guid'],
                                      base64_url_decode(info['guid_sig'])):
            log.error('import_xchannel: Unable to verify xchannel signature '
                      'for xchannel with hash {}'.format(channel_hash))
            raise ValueError('Unable to verify channel signature')

        if '/' in info['address']:
            parts = info['address'].split(sep='/')
            info['address'] = parts[0]

        if '@' in info['address']:
            parts = info['address'].split(sep='@')
            nickname = parts[0]
        else:
            nickname = info['address']

        xchannel_service = self['xchannel']
        try:
            xchannel = xchannel_service[channel_hash]
        except KeyError:
            xchannel = registry.content.create(
                'ZotRemoteXChannel',
                nickname=nickname,
                name=info['name'],
                channel_hash=channel_hash,
                guid=info['guid'],
                signature=info['guid_sig'],
                key=pub_key,
                address=info['address'],
                url=info['url'],
                connections_url=info['connections_url'],
                photo=None,
                flags=None,
                *arg, **kw)
            xchannel_service.add(channel_hash, xchannel)
        else:
            xchannel.update(info)

        for location in info.get('locations', []):
            try:
                self.import_hub(info, location, channel_hash=channel_hash,
                                registry=registry, pub_key=pub_key)
            except ValueError:
                continue

        # TODO import photos and profiles

        site = info.get('site', [])
        try:
            self.import_site(info, site,
                             registry=registry, pub_key=pub_key)
        except (KeyError, ValueError):
            pass

    def import_hub(self, info, location, *arg, **kw):
        registry = kw.pop('registry', None)
        if registry is None:
            registry = get_current_registry()

        channel_hash = kw.pop('channel_hash', None)
        if channel_hash is None:
            channel_hash = self._create_channel_hash(info['guid'],
                                                     info['guid_sig'])

        pub_key = kw.pop('pub_key', None)
        if pub_key is None:
            pub_key = PersistentRSAKey(extern_public_key=info['key'])
        if not pub_key.verify_message(location['url'],
                                      base64_url_decode(location['url_sig'])):
            log.error('import_hub: Unable to verify hub signature '
                      'for hub with hash {}'.format(channel_hash))
            raise ValueError('Unable to verify site signature')

        # TODO Allow channel clones
        if not location['primary']:
            return

        try:
            site_key = PersistentRSAKey(extern_public_key=location['sitekey'])
        except KeyError:
            raise ValueError('Empty hub site key')

        if '/' in location['address']:
            parts = info['address'].split(sep='/')
            location['address'] = parts[0]

        hub_service = self['hub']
        try:
            hub = hub_service[channel_hash]
        except KeyError:
            hub = registry.content.create(
                'ZotRemoteHub',
                channel_hash=channel_hash,
                guid=info['guid'],
                signature=info['guid_sig'],
                key=site_key,
                host=location['host'],
                address=location['address'],
                url=location['url'],
                url_signature=location['url_sig'],
                callback=location['callback'],
                *arg, **kw)
            hub_service.add(channel_hash, hub)
        else:
            hub.update(location)

    def import_site(self, info, site, *arg, **kw):
        registry = kw.pop('registry', None)
        if registry is None:
            registry = get_current_registry()

        url = site['url']

        pub_key = kw.pop('pub_key', None)
        if pub_key is None:
            pub_key = PersistentRSAKey(extern_public_key=info['key'])
        if not pub_key.verify_message(url,
                                      base64_url_decode(site['url_sig'])):
            log.error('import_site: Unable to verify site signature '
                      'for site {}'.format(url))
            raise ValueError('Unable to verify site signature')

        site_service = self['site']
        try:
            site = site_service[url]
        except KeyError:
            site = registry.content.create(
                'ZotSite',
                url=url,
                register_policy=site['register_policy'],
                access_policy=site['access_policy'],
                directory_mode=site['directory_mode'],
                directory_url=site['directory_url'],
                version=site['version'],
                admin_email=site['admin'],
                *arg, **kw)
            site_service.add(url, site)
        else:
            site.update(site)

    def zot_finger(self, url, channel_hash=None):
        result = {'success': False}

        if '@' in url:
            parts = url.split(sep='@')
            nickname = parts[0]
            netloc = parts[1]
        else:
            root = find_root(self)
            nickname = url
            netloc = root.netloc

        if (not nickname) or (not netloc):
            log.error('zot_finger: No valid address in URL %s' % url)
            raise ValueError('No valid address in URL %s' % url)

        xchannel_address = '@'.join([nickname, netloc])
        log.debug('zot_finger: xchannel_address = %s' % xchannel_address)

        hub_service = self['hub']
        xchannel_service = self['xchannel']

        scheme = 'https'
        well_known = '/.well-known/zot-info'
        query = ''
        payload = None
        request_method = requests.post

        for xchannel in xchannel_service.values():
            if xchannel.address == xchannel_address:
                try:
                    # Every xchannel should have a hub, but just in
                    # case something went wrong, no need to error out.
                    # We're just checking if one exists.
                    hub = hub_service[xchannel.channel_hash]
                except KeyError:
                    break
                url = urlparse(hub.url)
                scheme = url.scheme
                netloc = url.netloc
                log.debug('zot_finger: Found known hub using '
                          'scheme=%s, netloc=%s' % (scheme, netloc))
                break

        try:
            my_channel = xchannel_service[channel_hash]
            payload = {'address': nickname,
                       'target': my_channel.guid,
                       'target_sig': my_channel.signature,
                       'key': my_channel.key.export_public_key()}
            log.debug('zot_finger: payload = %s' % payload)
        except (KeyError, TypeError):
            request_method = requests.get
            query = 'address={}'.format(nickname)

        url = urlunparse((scheme, netloc, well_known, '', query, '',))
        log.debug('zot_finger: url = %s' % url)
        try:
            response = request_method(url, data=payload, verify=True,
                                      allow_redirects=True, timeout=3)
            log.debug('zot_finger: Response status code = %d' % (
                      response.status_code))
            if 400 <= response.status_code < 600:
                response.raise_for_status()
        except requests.exceptions.RequestException as e:
            log.error('zot_finger: Caught RequestException = %s' % str(e))
            if scheme != 'http':
                scheme = 'http'
                url = urlunparse((scheme, netloc, well_known, '', query, '',))
                log.debug('zot_finger: Falling back to HTTP at URL %s' % url)
                try:
                    response = request_method(url, data=payload, verify=True,
                                              allow_redirects=True, timeout=3)
                    log.debug('zot_finger: Response status code = %d' % (
                              response.status_code))
                    if 400 <= response.status_code < 600:
                        response.raise_for_status()
                except requests.exceptions.RequestException as f:
                    log.error('zot_finger: Caught RequestException = %s' % (
                              str(f)))
                    raise
                else:
                    log.debug('zot_finger: Inner request history = %s' % (
                              response.history))
                    if 200 <= response.status_code < 300:
                        result = response.json()
            else:
                # No need to retry if scheme was already HTTP
                raise
        else:
            log.debug('zot_finger: Outer request history = %s' % (
                      response.history))
            if 200 <= response.status_code < 300:
                result = response.json()

        # We only reach this point when we received a response with a 2xx
        # success status code
        log.debug('zot_finger: result =  %s' % result)
        if not result['success']:
            if 'message' in result:
                raise ValueError(result['message'])
            else:
                raise ValueError('No results')
        return result
