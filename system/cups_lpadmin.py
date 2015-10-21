#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2015, David Symons (Multimac) <Mult1m4c@gmail.com>
#
# This file is part of Ansible
#
# This module is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this software.  If not, see <http://www.gnu.org/licenses/>.

import json
import re
import shlex

DOCUMENTATION = '''
---
module: cups_lpadmin
author: David Symons (Multimac) <Mult1m4c@gmail.com>
short_description: Manages printers in CUPS via lpadmin
version_added: "2.0"
requirements:
  - CUPS 1.7+
description:
  - Creates, removes and sets options for printers in CUPS
options:
  state:
    choices: [present, absent]
    default: present
    description:
      - Whether the printer should or should not be in CUPS.
  dest:
    description:
      - The destination to configure or remove from CUPS
    required: true
  uri:
    default: None
    description:
      - The URI to use when connecting to the printer
      - This is only required in the present state
    required: false
  enabled:
    default: true
    description:
      - Whether or not the printer should be enabled and accepting jobs
    required: false
  shared:
    default: false
    description:
      - Whether or not the printer should be shared on the network
    required: false
  model:
    default: None
    description:
      - The System V interface or PPD file to be used for the printer
    required: false
  info:
    default: None
    description:
      - The textual description of the printer.
    required: false
  location:
    default: None
    description:
      - The textual location of the printer.
    required: false
  options:
    default: { }
    description:
      - A dictionary of key-value pairs describing printer options and their required value.
    required: false
'''

EXAMPLES = '''
# Creates a Zebra ZPL printer called zebra
- cups_lpadmin: state=present dest=zebra uri=192.168.1.2 model=drv:///sample.drv/zebra.ppd

# Updates the zebra printer with some custom options
- cups_lpadmin:
    state: present
    dest: zebra
    uri: 192.168.1.2
    model: drv:///sample.drv/zebra.ppd
    options:
      PageSize: w288h432

# Creates a raw printer called raw_test
- cups_lpadmin: state=present dest=raw_test uri=192.168.1.3

# Deletes the printers set up by the previous tasks
- cups_lpadmin: state=absent dest=zebra
- cups_lpadmin: state=absent dest=raw_test
'''

class CUPSPrinter(object):

    def __init__(self, module):
        self.module = module

        self.destination = module.params['dest']
        self.uri = module.params['uri']

        self.enabled = module.params['enabled']
        self.shared = module.params['shared']

        self.model = module.params['model']

        self.info = module.params['info']
        self.location = module.params['location']

        self.options = module.params['options']

        # Use lpd if a protocol is not specified
        if self.uri and '://' not in self.uri:
            self.uri = 'lpd://{0}/'.format(self.uri)

    def _get_installed_drivers(self):
        cmd = ['lpinfo', '-l', '-m']
        (rc, out, err) = self.module.run_command(cmd)

        # We want to split on sections starting with "Model:" as that specifies
        # a new available driver
        prog = re.compile("^Model:", re.MULTILINE)
        cups_drivers = re.split(prog, out)

        drivers = { }
        for d in cups_drivers:

            # Skip if the line contains only whitespace
            if not d.strip():
                continue

            curr = { }
            for l in d.splitlines():
                kv = l.split('=', 1)

                # Strip out any excess whitespace from the key/value
                kv = map(str.strip, kv)

                curr[kv[0]] = kv[1]

            # If no protocol is specified, then it must be on the local filesystem
            # By default there is no preceeding '/' on the path, so it must be prepended
            if not '://' in curr['name']:
                curr['name'] = '/{0}'.format(curr['name'])

            # Store drivers by their 'name' (i.e. path to driver file)
            drivers[curr['name']] = curr

        return drivers

    def _get_make_and_model(self):
        if not self.model:
            # We're dealing with a raw printer then
            return "Local Raw Printer"

        installed_drivers = self._get_installed_drivers()
        if self.model in installed_drivers:
            return installed_drivers[self.model]['make-and-model']

        self.module.fail_json(msg="unable to determine printer make and model")

    def _install_printer(self):
        cmd = [ 'lpadmin',
                '-p', self.destination,
                '-v', self.uri ]

        if self.enabled:
            cmd.append('-E')

        if self.shared:
            cmd.extend(['-o', 'printer-is-shared=true'])
        else:
            cmd.extend(['-o', 'printer-is-shared=false'])

        if self.model:
            cmd.extend(['-m', self.model])

        if self.info:
            cmd.extend(['-D', self.info])
        if self.location:
            cmd.extend(['-L', self.location])

        return self.module.run_command(cmd)

    def _install_printer_options(self):
        cmd = [ 'lpadmin',
                '-p', self.destination ]

        for k, v in self.options.iteritems():
            cmd.extend(['-o', '{0}={1}'.format(k, v)])

        return self.module.run_command(cmd)

    def _uninstall_printer(self):
        cmd = ['lpadmin', '-x', self.destination]
        return self.module.run_command(cmd)

    def get_printer_cups_options(self):
        """Returns the CUPS options for the printer"""
        cmd = ['lpoptions', '-p', self.destination]
        (rc, out, err) = self.module.run_command(cmd)

        options = { }
        for s in shlex.split(out):
            kv = s.split('=', 1)

            if len(kv) == 1: # If we only have an option name, set it's value to None
                options[kv[0]] = None
            elif len(kv) == 2: # Otherwise set it's value to what we received
                options[kv[0]] = kv[1]

        return options

    def get_printer_specific_options(self):
        """Returns the printer specific options for the printer, as well as the accepted options"""
        cmd = ['lpoptions', '-p', self.destination, '-l']
        (rc, out, err) = self.module.run_command(cmd)

        options = { }
        for l in out.splitlines():
            remaining = l

            (name, remaining) = remaining.split('/', 1)
            (label, remaining) = remaining.split(':', 1)

            values = shlex.split(remaining)

            current_value = None
            for v in values:
                # Current value is prepended with a '*'
                if not v.startswith('*'):
                    continue

                v = v[1:] # Strip the '*' from the value

                current_value = v
                break

            options[name] = {
                'current': current_value,
                'label': label,
                'values': values,
            }

        return options

    def check_cups_options(self):
        expected_cups_options = {
            'device-uri': self.uri,
            'printer-make-and-model': self._get_make_and_model(),

            'printer-location': self.location,
        }

        # 'printer-info' defaults to the destination name if not specified manually
        if self.info:
            expected_cups_options['printer-info'] = self.info
        else:
            expected_cups_options['printer-info'] = self.destination

        if self.shared:
            expected_cups_options['printer-is-shared'] = 'true'
        else:
            expected_cups_options['printer-is-shared'] = 'false'

        cups_options = self.get_printer_cups_options()
        for k in expected_cups_options:
            if k not in cups_options:
                return False

            if expected_cups_options[k] != cups_options[k]:
                return False

        return True

    def check_printer_options(self):
        expected_printer_options = self.options

        printer_options = self.get_printer_specific_options()
        for k in expected_printer_options:
            if k not in printer_options:
                return False

            if expected_printer_options[k] != printer_options[k]['current']:
                return False

        return True

    def exists(self):
        cmd = ['lpstat', '-p', self.destination]
        (rc, out, err) = self.module.run_command(cmd)

        # This command will fail if the destination doesn't exist (rc != 0)
        return rc == 0

    def install(self):
        if self.uri is None:
            self.module.fail_json(msg="'uri' is required for present state")

        rc = None
        out = ''
        err = ''

        if self.exists() and not self.check_cups_options():
            (rc, uninstall_out, uninstall_err) = self._uninstall_printer()

            out = (out + '\n' + uninstall_out).strip('\n')
            err = (err + '\n' + uninstall_err).strip('\n')

        if not self.exists():
            (rc, install_out, install_err) = self._install_printer()

            out = (out + '\n' + install_out).strip('\n')
            err = (err + '\n' + install_err).strip('\n')

        if not self.check_printer_options():
            (rc, options_out, options_err) = self._install_printer_options()

            out = (out + '\n' + options_out).strip('\n')
            err = (err + '\n' + options_err).strip('\n')

        return (rc, out, err)

    def uninstall(self):
        rc = None
        out = ''
        err = ''

        if self.exists():
            (rc, out, err) = self._uninstall_printer()

        return (rc, out, err)


def main():
    module = AnsibleModule(
        argument_spec = dict(
            state=dict(default='present', choices=['present', 'absent'], type='str'),
            dest=dict(required=True, type='str'),
            uri=dict(default=None, type='str'),
            enabled=dict(default=True, type='bool'),
            shared=dict(default=False, type='bool'),
            model=dict(default=None, type='str'),
            info=dict(default=None, type='str'),
            location=dict(default=None, type='str'),
            options=dict(default={ }, type='dict'),
        ),
        supports_check_mode=False
    )

    cups_printer = CUPSPrinter(module)

    rc = None
    out = ''
    err = ''

    result = { }
    result['state'] = module.params['state']
    result['destination'] = cups_printer.destination

    state = module.params['state']
    if state == 'present':
        (rc, out, err) = cups_printer.install()
    elif state == 'absent':
        (rc, out, err) = cups_printer.uninstall()

    if rc is None:
        result['changed'] = False
    else:
        result['changed'] = True

    if out:
        result['stdout'] = out
    if err:
        result['stderr'] = err

    module.exit_json(**result)


from ansible.module_utils.basic import *

if __name__ == '__main__':
    main()