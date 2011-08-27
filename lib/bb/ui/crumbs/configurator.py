#
# BitBake Graphical GTK User Interface
#
# Copyright (C) 2011        Intel Corporation
#
# Authored by Joshua Lock <josh@linux.intel.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import gobject
import copy
import re, os, ConfigParser, tempfile
from bb import data

class Configurator(gobject.GObject):

    """
    A GObject to handle writing modified configuration values back
    to conf files.
    """
    __gsignals__ = {
        "layers-loaded"  : (gobject.SIGNAL_RUN_LAST,
                           gobject.TYPE_NONE,
                           ()),
        "layers-changed" : (gobject.SIGNAL_RUN_LAST,
                            gobject.TYPE_NONE,
                            ())
    }

    def __init__(self):
        gobject.GObject.__init__(self)

        self.bblayers = None
        self.enabled_layers = {}
        self.loaded_layers = {}

        # Is it safe to assume .config exists?
        confdir = os.path.expanduser("~/.config")
        bb.utils.mkdirhier(confdir)
        self.hob_conf_path = os.path.join(confdir, "hob.cfg")
        self.hob_conf = ConfigParser.RawConfigParser()
        if not os.path.exists(self.hob_conf_path):
            self.create_initial_hob_conf()
        self.hob_conf.read(self.hob_conf_path)

    def create_initial_hob_conf(self):
        self.hob_conf.add_section('hob')
        self.hob_conf.set('hob', 'HobConfVersion', '1')
        tempdir = os.path.join(tempfile.gettempdir(), 'hob-images')
        self.hob_conf.set('hob', 'HobRecipePath', tempdir)
        self.hob_conf.set('hob', 'BuildToolchain', 'false')
        self.hob_conf.set('hob', 'BuildToolchainHeaders', 'false')
        self.hob_conf.add_section('BitBake')
        bb.utils.mkdirhier(tempdir)
        self.write_hob_conf(False)

    def write_hob_conf(self, backup=True):
        if backup:
            bkup = "%s~" % self.hob_conf_path
            os.rename(self.hob_conf_path, bkup)

        with open(self.hob_conf_path, 'w') as conf_file:
            self.hob_conf.write(conf_file)

    def get_conf_string(self, confvar):
        try:
            val = self.hob_conf.get('BitBake', confvar)
            return val
        except ConfigParser.NoOptionError:
            return None

    def set_conf_string(self, confvar, confval):
        self.hob_conf.set('BitBake', confvar, confval)

    # NOTE: cribbed from the cooker...
    def _parse(self, f, data, include=False):
        try:
            return bb.parse.handle(f, data, include)
        except (IOError, bb.parse.ParseError) as exc:
            parselog.critical("Unable to parse %s: %s" % (f, exc))
            sys.exit(1)

    def _loadLayerConf(self, path):
        self.bblayers = path
        self.enabled_layers = {}
        self.loaded_layers = {}
        data = bb.data.init()
        data = self._parse(self.bblayers, data)
        layers = (bb.data.getVar('BBLAYERS', data, True) or "").split()
        for layer in layers:
            # TODO: we may be better off calling the layer by its
            # BBFILE_COLLECTIONS value?
            name = self._getLayerName(layer)
            self.loaded_layers[name] = layer

        self.enabled_layers = copy.deepcopy(self.loaded_layers)
        self.emit("layers-loaded")

    def _addConfigFile(self, path):
        pref, sep, filename = path.rpartition("/")
        if filename == "bblayers.conf":
            self._loadLayerConf(path)

    def _splitLayer(self, path):
        # we only care about the path up to /conf/layer.conf
        layerpath, conf, end = path.rpartition("/conf/")
        return layerpath

    def _getLayerName(self, path):
        # Should this be the collection name?
        layerpath, sep, name = path.rpartition("/")
        return name

    def disableLayer(self, layer):
        if layer in self.enabled_layers:
            del self.enabled_layers[layer]

    def addLayerConf(self, confpath):
        layerpath = self._splitLayer(confpath)
        name = self._getLayerName(layerpath)

        if not layerpath or not name:
            return None, None
        elif name not in self.enabled_layers:
            self.addLayer(name, layerpath)
            return name, layerpath
        else:
            return name, None

    def addLayer(self, name, path):
        self.enabled_layers[name] = path

    def _isLayerConfDirty(self):
        # if a different number of layers enabled to what was
        # loaded, definitely different
        if len(self.enabled_layers) != len(self.loaded_layers):
            return True

        for layer in self.loaded_layers:
            # if layer loaded but no longer present, definitely dirty
            if layer not in self.enabled_layers:
                return True

        for layer in self.enabled_layers:
            # if this layer wasn't present at load, definitely dirty
            if layer not in self.loaded_layers:
                return True
            # if this layers path has changed, definitely dirty
            if self.enabled_layers[layer] != self.loaded_layers[layer]:
                return True

        return False

    def _constructLayerEntry(self):
        """
        Returns a string representing the new layer selection
        """
        layers = self.enabled_layers.copy()
        # Construct BBLAYERS entry
        layer_entry = "BBLAYERS = \" \\\n"
        if 'meta' in layers:
            layer_entry = layer_entry + "  %s \\\n" % layers['meta']
            del layers['meta']
        for layer in layers:
            layer_entry = layer_entry + "  %s \\\n" % layers[layer]
        layer_entry = layer_entry + "  \""

        return "".join(layer_entry)

    def writeConfFile(self, conffile, contents):
        """
        Make a backup copy of conffile and write a new file in its stead with
        the lines in the contents list.
        """
        # Create a backup of the conf file
        bkup = "%s~" % conffile
        os.rename(conffile, bkup)

        # Write the contents list object to the conf file
        with open(conffile, "w") as new:
            new.write("".join(contents))

    def writeLayerConf(self):
        # If we've not added/removed new layers don't write
        if not self._isLayerConfDirty():
            return

        # This pattern should find the existing BBLAYERS
        pattern = 'BBLAYERS\s=\s\".*\"'

        replacement = self._constructLayerEntry()

        with open(self.bblayers, "r") as f:
            contents = f.read()
            p = re.compile(pattern, re.DOTALL)
            new = p.sub(replacement, contents)

        self.writeConfFile(self.bblayers, new)

        # set loaded_layers for dirtiness tracking
        self.loaded_layers = copy.deepcopy(self.enabled_layers)

        self.emit("layers-changed")

    def configFound(self, handler, path):
        self._addConfigFile(path)

    def loadConfig(self, path):
        self._addConfigFile(path)
