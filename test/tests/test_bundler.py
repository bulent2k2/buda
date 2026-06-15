# Copyright 2026 Ben Bulent Basaran
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pytest
from pytest_bdd import scenarios, given, when, then, parsers
import buda

scenarios('features/bundler_logic.feature')

@pytest.fixture
def context():
    return {
        'netlist': buda.Netlist(),
        'bundler': buda.Bundler(),
        'results': []
    }

# --- GIVEN Steps ---
@given('a netlist with nets:')
def setup_netlist(context, datatable):
    # datatable.raw() → list[list[str]]; row 0 is the header
    for net_name, driver, receivers in datatable[1:]:
        receiver_list = [r.strip() for r in receivers.split(',')]
        context['netlist'].add_net(net_name, driver, receiver_list)

# --- WHEN Steps ---
@when(parsers.parse('I run the bundler with "{strategy}"'))
def run_bundler_with_strategy(context, strategy):
    if strategy == "strict_connectivity":
        context['bundler'].set_strategy(buda.Strategy.STRICT)
    elif strategy == "shared_receivers_only":
        context['bundler'].set_strategy(buda.Strategy.CONVERGENT)
    context['results'] = context['bundler'].run(context['netlist'])

@when('I run the bundler')
def run_bundler(context):
    context['results'] = context['bundler'].run(context['netlist'])

# --- THEN Steps ---
@then(parsers.parse('I should get {count:d} bundle containing "{nets}"'))
def verify_bundle_count_and_content(context, count, nets):
    assert len(context['results']) == count
    expected_nets = sorted([n.strip() for n in nets.split(',')])
    actual_nets = sorted(context['results'][0].get_net_names())
    assert actual_nets == expected_nets

@then(parsers.parse('I should get {count:d} bundles'))
def verify_bundle_count(context, count):
    assert len(context['results']) == count

@then(parsers.parse('the bundle reason should be "{reason}"'))
def verify_reason(context, reason):
    assert context['results'][0].reason == reason
