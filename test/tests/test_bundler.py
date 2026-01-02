import pytest
from pytest_bdd import scenarios, given, when, then, parsers
# This imports our C++ bound module (we will build this next)
import interconnect

# Load the feature file
scenarios('features/bundler_logic.feature')

# --- Fixtures to hold state ---
@pytest.fixture
def context():
    return {
        'netlist': interconnect.Netlist(),
        'bundler': interconnect.Bundler(),
        'results': []
    }

# --- GIVEN Steps ---
@given(parsers.parse('a netlist with nets:\n{table}'))
def setup_netlist(context, table):
    # Parse the Gherkin table and populate the C++ Netlist object
    # Table format: | Net Name | Driver | Receivers |
    rows = table.split('\n')[1:] # Skip header
    for row in rows:
        parts = [p.strip() for p in row.split('|') if p.strip()]
        if not parts: continue
        
        net_name, driver, receivers = parts
        receiver_list = [r.strip() for r in receivers.split(',')]
        
        # Call C++ method to add net
        context['netlist'].add_net(net_name, driver, receiver_list)

# --- WHEN Steps ---
@when(parsers.parse('I run the bundler with "{strategy}"'))
def run_bundler(context, strategy):
    # Configure the C++ Bundler
    if strategy == "strict_connectivity":
        context['bundler'].set_strategy(interconnect.Strategy.STRICT)
    elif strategy == "shared_receivers_only":
        context['bundler'].set_strategy(interconnect.Strategy.CONVERGENT)
        
    # Run the engine
    context['results'] = context['bundler'].run(context['netlist'])

# --- THEN Steps ---
@then(parsers.parse('I should get {count:d} bundle containing "{nets}"'))
def verify_bundle_count_and_content(context, count, nets):
    assert len(context['results']) == count
    
    # Verify content of the first bundle matches expected nets
    expected_nets = sorted([n.strip() for n in nets.split(',')])
    actual_nets = sorted(context['results'][0].get_net_names())
    
    assert actual_nets == expected_nets

@then(parsers.parse('the bundle reason should be "{reason}"'))
def verify_reason(context, reason):
    assert context['results'][0].reason == reason
