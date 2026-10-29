from decimal import Decimal

import pytest

from hunter.comparative_valuation.ethereum_rpc import SKY_TOKEN, TRANSFER_TOPIC
from hunter.comparative_valuation.sky_payment_provenance import (
    BUYBACK_POOL,
    BUYBACK_TRADER,
    SKY_TREASURY,
    USDS_TOKEN,
    ZERO,
    attest_sky_buyback_payment,
)


def _log(token, sender, receiver, amount, index):
    return {
        'address': token, 'topics': [TRANSFER_TOPIC, '0x' + sender[2:].rjust(64, '0'), '0x' + receiver[2:].rjust(64, '0')],
        'data': hex(int(Decimal(amount)*10**18)), 'logIndex': hex(index),
        'transactionHash': '0x'+'a'*64, 'blockHash': '0x'+'b'*64, 'blockNumber': '0x10',
    }


def _receipt():
    return {'status': '0x1', 'transactionHash': '0x'+'a'*64, 'blockHash': '0x'+'b'*64, 'blockNumber': '0x10', 'logs': [
        _log(USDS_TOKEN, ZERO, BUYBACK_TRADER, '3300', 1),
        _log(USDS_TOKEN, BUYBACK_TRADER, BUYBACK_POOL, '3300', 2),
        _log(SKY_TOKEN, BUYBACK_POOL, SKY_TREASURY, '49300.380794520253387453', 3),
    ]}


def test_verified_payment_is_not_verified_burn_cost_basis():
    result = attest_sky_buyback_payment(_receipt(), source_endpoint='alchemy')
    assert result.usds_paid == Decimal('3300')
    assert result.sky_received == Decimal('49300.380794520253387453')
    with pytest.raises(ValueError, match='not uniquely linked'):
        result.require_burn_cost_basis()


@pytest.mark.parametrize('alter', ['no_mint', 'wrong_amount', 'wrong_treasury', 'reverse_order', 'bad_provenance'])
def test_reject_incomplete_provenance(alter):
    r = _receipt()
    if alter == 'no_mint':
        r['logs'].pop(0)
    elif alter == 'wrong_amount':
        r['logs'][1]['data'] = hex(1)
    elif alter == 'wrong_treasury':
        r['logs'][2]['topics'][2] = '0x' + '1'*64
    elif alter == 'reverse_order':
        r['logs'][0]['logIndex'] = '0x4'
    else:
        r['logs'][1]['blockHash'] = '0x' + 'c'*64
    with pytest.raises(ValueError):
        attest_sky_buyback_payment(r, source_endpoint='alchemy')
