# This file is part of the MapProxy project.
# Copyright (C) 2011 Omniscale <http://omniscale.de>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import with_statement, absolute_import

import sys
import time
import os
from cStringIO import StringIO

from mapproxy.grid import tile_grid
from mapproxy.image import ImageSource
from mapproxy.image.opts import ImageOptions
from mapproxy.layer import MapExtent, DefaultMapExtent, BlankImage, MapLayer
from mapproxy.source import  SourceError
from mapproxy.client.log import log_request
from mapproxy.util import reraise_exception
from mapproxy.util.async import run_non_blocking

try:
    import mapnik
    mapnik
except ImportError:
    mapnik = None

try:
    import mapnik2
    mapnik2
except ImportError:
    mapnik2 = None


import logging
log = logging.getLogger(__name__)

valid_extensions = ['.ttf','.otf','.ttc','.pfa','.pfb','.ttc','.dfont']

class MapnikSource(MapLayer):
    supports_meta_tiles = True
    mapnik = mapnik

    def __init__(self, mapfile, layers=None, image_opts=None, coverage=None, res_range=None, lock=None, fonts_dir=None, scale_factor=1):
        log.error(image_opts)
        MapLayer.__init__(self, image_opts=image_opts)
        self.mapfile = mapfile
        self.coverage = coverage
        self.res_range = res_range
        self.layers = set(layers) if layers else None
        self.lock = lock
        self.fonts_dir = fonts_dir
        self.added = []
        self.failed = []
        self.scale_factor = scale_factor

        if self.fonts_dir:
          self.add_fonts([self.fonts_dir])
        if self.coverage:
            self.extent = MapExtent(self.coverage.bbox, self.coverage.srs)
        else:
            self.extent = DefaultMapExtent()

    def get_map(self, query):
        if self.res_range and not self.res_range.contains(query.bbox, query.size,
                                                          query.srs):
            raise BlankImage()
        if self.coverage and not self.coverage.intersects(query.bbox, query.srs):
            raise BlankImage()

        try:
            resp = self.render(query)
        except RuntimeError, ex:
            log.error('could not render Mapnik map: %s', ex)
            reraise_exception(SourceError(ex.args[0]), sys.exc_info())
        resp.opacity = self.opacity
        return resp

    def render(self, query):
        mapfile = self.mapfile
        if '%(webmercator_level)' in mapfile:
            _bbox, level = tile_grid(3857).get_affected_bbox_and_level(
                query.bbox, query.size, req_srs=query.srs)
            mapfile = mapfile % {'webmercator_level': level}

        if self.lock:
            with self.lock():
                return self.render_mapfile(mapfile, query)
        else:
            return self.render_mapfile(mapfile, query)

    def render_mapfile(self, mapfile, query):
        return run_non_blocking(self._render_mapfile, (mapfile, query))

    def _render_mapfile(self, mapfile, query):
        start_time = time.time()
        data = None
        try:
            m = self.mapnik.Map(query.size[0], query.size[1])
            self.mapnik.load_map(m, str(mapfile))
            m.srs = '+init=%s' % str(query.srs.srs_code.lower())
            envelope = self.mapnik.Envelope(*query.bbox)
            m.zoom_to_box(envelope)

            if self.layers:
                i = 0
                for layer in m.layers[:]:
                    if layer.name != 'Unkown' and layer.name not in self.layers:
                        del m.layers[i]
                    else:
                        i += 1

            img = self.mapnik.Image(query.size[0], query.size[1])
            self.mapnik.render(m, img, self.scale_factor)
            data = img.tostring(str(query.format))
        finally:
            size = None
            if data:
                size = len(data)
            log_request('%s:%s:%s:%s' % (mapfile, query.bbox, query.srs.srs_code, query.size),
                status='200' if data else '500', size=size, method='API', duration=time.time()-start_time)

        return ImageSource(StringIO(data), size=query.size,
            image_opts=ImageOptions(transparent=self.transparent, format=query.format))

    def add_fonts(self,fonts):
        engine = mapnik.FontEngine.instance()
        for font in fonts:
            if os.path.isdir(font):
                found = False;
                log.error(font)
                for dirpath, _, filenames in os.walk(font):
                    for filename in filenames:
                        if os.path.splitext(filename)[1] in valid_extensions:
                            font_name = os.path.join(dirpath, filename)
                            if engine.register_font(font_name):
                                self.added.append(font_name)
                            else:
                                self.failed.append(font_name)
            else:
                if engine.register_font(font):
                    self.added.append(font)
                else:
                    self.failed.append(font)

class Mapnik2Source(MapnikSource):
    mapnik = mapnik2

