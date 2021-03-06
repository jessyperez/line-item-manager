from pprint import pformat

from .config import config, VERBOSE1, VERBOSE2
from .exceptions import ResourceNotFound
from .operations import Advertiser, AdUnit, Placement, TargetingKey, TargetingValues, \
     CreativeBanner, CreativeVideo, Order, CurrentNetwork, CurrentUser, LineItem, LICA
from .template import render_cfg, render_src
from .utils import format_long_list

logger = config.getLogger(__name__)

def log(objname, obj=None):
    logger.log(VERBOSE1, '%s:\n%s', objname, pformat(obj if obj else config.user.get(objname, {})))

def target(key, names, match_type='EXACT'):
    tgt_key = TargetingKey(name=key).fetchone(create=True)
    recs = []
    for name in names:
        recs.append(dict(
            customTargetingKeyId=tgt_key['id'],
            name=name,
            displayName=name,
            matchType=match_type,
        ))
    tgt_values = TargetingValues(key_id=tgt_key['id']).fetch(create=True, recs=recs, validate=True)
    return dict(
        key=tgt_key,
        values=tgt_values,
        names={v['name']:v for v in tgt_values}
    )

class GAMConfig:

    _ad_units = None
    _network = None
    _placements = None
    _targeting_custom = None
    _user = None

    def __init__(self):
        _ = [log(i_) for i_ in ('targeting', 'rate')]
        self._li_objs = []
        self._lica_objs = []
        self._success = False

    @property
    def li_objs(self):
        return self._li_objs

    @property
    def lica_objs(self):
        return self._lica_objs

    @property
    def ad_units(self):
        if self._ad_units is None:
            self._ad_units = []
            for name in config.user.get('targeting', {}).get('ad_unit_names', []):
                ad_unit = AdUnit(name=name).fetchone()
                if not ad_unit:
                    raise ResourceNotFound(f'Ad Unit named \'{name}\' was not found')
                self._ad_units.append(ad_unit)
        return self._ad_units

    def add_li_obj(self, media_type, bidder_code, cpms):
        self._li_objs.append(GAMLineItems(self, media_type, bidder_code, cpms))
        return self._li_objs[-1]

    def archive(self):
        order_ids = [i_.order['id'] for i_ in self._li_objs]
        if order_ids:
            logger.info('Auto-archiving Orders:\n%s', pformat(order_ids))
            response = Order(id=order_ids).archive()
            changes = response['numChanges'] if 'numChanges' in response else None
            if not changes == len(order_ids):
                logger.error('Order archive, %s, of %d changes, reported %s changes',
                             order_ids, len(order_ids), changes)

    def cleanup(self):
        if not self.success and not config.cli['skip_auto_archive']:
            self.archive()

    def check_resources(self):
        _ = self.ad_units
        _ = self.placements

    def create_line_items(self):
        self.check_resources()
        for bidder_code in config.bidder_codes():
            logger.info('#' * 80)
            logger.info('Bidder: name="%s", code="%s"',
                        config.bidder_name(bidder_code), bidder_code)
            logger.info('Key: "%s", Values: %s',
                        config.targeting_key(bidder_code), format_long_list(config.cpm_names()))
            for media_type in config.media_types():
                logger.info('#' * 60)
                logger.info('Media Type: "%s"', media_type)
                for cpms in config.cpm_names_batched():
                    logger.info('Line Items: CPMs(min=%s, max=%s, cnt=%d)',
                                cpms[0], cpms[-1], len(cpms))
                    li_ = self.add_li_obj(media_type, bidder_code, cpms)
                    logger.info('Line Item Creative Associations: Creative Count=%d',
                                len(li_.creatives))
                    self._lica_objs.append(li_.create())

    @property
    def network(self):
        if self._network is None:
            self._network = CurrentNetwork().fetch()
        return self._network

    @property
    def placements(self):
        if self._placements is None:
            self._placements = []
            for name in config.user.get('targeting', {}).get('placement_names', []):
                placement = Placement(name=name).fetchone()
                if not placement:
                    raise ResourceNotFound(f'Placement named \'{name}\' was not found')
                self._placements.append(placement)
        return self._placements

    @property
    def success(self):
        return self._success

    @success.setter
    def success(self, val):
        self._success = val

    @property
    def targeting_custom(self):
        if self._targeting_custom is None:
            self._targeting_custom = [target(k, v) for k, v in config.custom_targeting_key_values()]
        return self._targeting_custom

    @property
    def user(self):
        if self._user is None:
            self._user = CurrentUser().fetch()
        return self._user

class GAMLineItems:

    _advertiser = None
    _creatives = None
    _order = None
    _targeting_key = None
    _line_items = None

    def __init__(self, gam: GAMConfig, media_type, bidder_code, cpms):
        self.gam = gam
        self.media_type = media_type
        self.bidder_code = bidder_code
        self.cpms = cpms
        self.atts = dict(
            bidder_code=bidder_code,
            media_type=media_type,
        )

    @property
    def advertiser(self):
        if self._advertiser is None:
            cfg = render_cfg('advertiser', bidder_code=self.bidder_code)
            log('advertiser', obj=cfg)
            self._advertiser = \
              Advertiser(name=cfg['name']).fetchone(create=True)
        return self._advertiser

    def create(self):
        recs = []
        for line_item in self.line_items:
            for creative in self.creatives:
                recs.append(dict(lineItemId=line_item['id'], creativeId=creative['id']))
        return LICA().create(recs, validate=True)

    @property
    def creatives(self):
        if self._creatives is None:
            cfg = render_cfg('creative', **self.atts)
            _name = f'creative_{self.media_type}'
            _method = getattr(self, _name)
            log(_name, obj={k:cfg[k] for k in ('name', self.media_type)})
            self._creatives = [_method(cfg, size) for size in cfg[self.media_type]['sizes']]
        return self._creatives

    def creative_banner(self, cfg, size):
        params = dict(
            name=cfg['name'],
            advertiserId=self.advertiser['id'],
            size=size,
            snippet=cfg['banner']['snippet'],
            isSafeFrameCompatible=cfg['banner'].get('safe_frame', True),
        )
        return CreativeBanner(**params).fetchone(create=True)

    def creative_video(self, cfg, size):
        params = dict(
            name=cfg['name'],
            advertiserId=self.advertiser['id'],
            size=size,
            vastXmlUrl=cfg['video']['vast_xml_url'],
        )
        return CreativeVideo(**params).fetchone(create=True)

    @property
    def line_items(self):
        if self._line_items is None:
            recs = []
            src = config.read_package_file('line_item_template.yml')
            for i_, cpm in enumerate(self.cpms):
                li_cfg = render_cfg('line_item', cpm=cpm, **self.atts)
                if (i_ == 0) or (i_ == len(self.cpms) - 1) or config.isLoggingEnabled(VERBOSE2):
                    log('line_item', obj=li_cfg)
                params = dict(
                    micro_amount=config.micro_amount(cpm),
                    cpm=cpm,
                    li=self,
                    li_cfg=li_cfg,
                    user_cfg=config.user,
                )
                recs.append(render_src(src, **params))
            self._line_items = LineItem().create(recs, validate=True)
        return self._line_items

    @property
    def order(self):
        if self._order is None:
            cfg = render_cfg('order', cpm_min=self.cpms[0], cpm_max=self.cpms[-1], **self.atts)
            log('order', obj=cfg)
            self._order = Order(name=cfg['name'], advertiserId=self.advertiser['id'],
                                traffickerId=self.gam.user['id']).fetchone(create=True)
        return self._order

    @property
    def targeting_key(self):
        if self._targeting_key is None:
            self._targeting_key = target(config.targeting_key(self.bidder_code), config.cpm_names())
        return self._targeting_key
