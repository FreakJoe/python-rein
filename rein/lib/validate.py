#!/usr/bin/env python
import re
import bitcoinecdsa
import unittest
import click
import requests
import time
from block import Block

def filter_out_expired(rein, user, urls, jobs):
    '''
    Filters out expired jobs and those that have no expiration set.
    If a clock hash is included, use 14 day default for expiration.
    '''
    live = []
    times = {}
    click.echo('Verifying block times...')
    with click.progressbar(jobs) as jobs_with_progress:
        for j in jobs_with_progress:
            if 'Clock hash' not in j:
                continue
            block_hash = j['Clock hash']
            if 'Expiration (days)' not in j:
                continue

            # get cached time of block if we have it
            if Block.get_time(rein, block_hash):
                times[block_hash] = Block.get_time(rein, block_hash)

            # otherwise we're going to ask each server for it
            elif block_hash not in times:
                # request block info for the clock hash
                for url in urls:
                    sel_url = url + 'bitcoin?owner={0}&query=getbyhash&hash={1}'
                    try:
                        answer = requests.get(url=sel_url.format(user.maddr, block_hash))
                    except requests.exceptions.ConnectionError:
                        click.echo('Could not reach %s.' % url)

                    data = answer.json()
                    if not Block.get_time(rein, block_hash):
                        b = Block(block_hash, data['time'], data['height'])
                        rein.session.add(b)
                        rein.session.commit()

    for j in jobs:
        if 'Clock hash' not in j:
            continue
        block_hash = j['Clock hash']
        try:
            expiration = int(j['Expiration (days)'])*86400
        except:
            expiration = 14*86400
        if int(times[block_hash]) == int(j['Time']):
            if times[block_hash] + expiration > int(time.time()):
                live.append(j)
    return live


def choose_best_block(blocks):
    '''
    Given an array of blocks, returns the hash and time of the
    most frequent in the array.
    '''
    hashes = []
    times = {}
    for b in blocks:
        hashes.append(b['hash'])
    if len(hashes) == 0:
        return None, None
    the_hash = max(set(hashes), key=hashes.count)
    for b in blocks:
        if the_hash == b['hash']:
            the_time = b['time']
    return the_hash, the_time


def strip_armor(sig, dash_space=False):
    '''Removes ASCII-armor from a signed message by default exlcudes 'dash-space' headers'''
    sig = sig.replace('- ----', '-' * 5) if dash_space else sig
    sig = re.sub("-{5}BEGIN BITCOIN SIGNED MESSAGE-{5}", "", sig)
    sig = re.sub(
        "\n+-{5}BEGIN SIGNATURE-{5}[\n\dA-z+=/]+-{5}END BITCOIN SIGNED MESSAGE-{5}\n*",
        "",
        sig
    )
    sig = re.sub("^\n", "", sig)
    sig = re.sub("\n\n", "", sig)
    return sig


def parse_document(document):
    ret = {}
    m = re.search('(Rein .*)\n', document)
    if m:
        ret['Title'] = m.group(1)
    matches = re.finditer("(.+?):\s(.+)(\n|$)", document)
    for match in matches:
        ret[match.group(1)] = match.group(2)
    return ret


def parse_sig(sig):
    '''
    Takes an ASCII-armored signature and returns a dictionary of its info.
    Returns the signature string, the signing key, and all of the information
    assigned within the message, for example:
       parse_sig(sig)['Name/handle'] === "David Sterry"
    '''
    ret = {}
    m = re.search('\n(Rein .*)\n', sig)
    if m:
        ret['Title'] = m.group(1)
    matches = re.finditer("(.+?):\s(.+)\n", sig)
    for match in matches:
        ret[match.group(1)] = match.group(2)
    m = re.search(
        "-{5}BEGIN SIGNATURE-{5}\n([A-z\d=+/]+)\n([A-z\d=+/]+)"
        "\n-{5}END BITCOIN SIGNED MESSAGE-{5}",
        sig
    )
    if m:
        ret['signature_address'] = m.group(1)
        ret['signature'] = m.group(2)
    else:
        return False
    return ret


def filter_valid_sigs(rein, docs, expected_field=None):
    valid = []
    fails = 0
    for m in docs:
        data = verify_sig(m)
        if expected_field:
            if data['valid'] and expected_field in data:
                valid.append(m)
            else:
                fails += 1
        else:
            if data['valid']:
                valid.append(m)
            else:
                fails += 1
    rein.log.info('fvs spammy fails = %d' % fails)
    return valid


def filter_and_parse_valid_sigs(rein, docs, expected_field=None):
    valid = []
    fails = 0
    for m in docs:
        data = verify_sig(m)
        if expected_field:
            if data['valid'] and expected_field in data:
                valid.append(data)
            else:
                fails += 1
        else:
            if data['valid']:
                valid.append(data)
            else:
                fails += 1
    rein.log.info('fapvs spammy fails = %d' % fails)
    return valid


def verify_sig(sig):
    '''The base function for verifying an ASCII-armored signature.'''
    sig_info = parse_sig(sig)
    if sig_info:
        message = strip_armor(sig)
        valid = bitcoinecdsa.verify(
            sig_info['signature_address'],
            message,
            sig_info['signature']
        )
    else:
        valid = False
    if sig_info:
        sig_info['valid'] = valid
    else:
        sig_info = {'valid': False}
    return sig_info


def validate_enrollment(enrollment_signature_text):
    a = verify_sig(enrollment_signature_text)
    if a['valid'] and a['signature_address'] == a['Master signing address']:
        return a
    else:
        return False


def validate_review(reviewer_text):
    a = verify_sig(reviewer_text)
    return [
        a['valid'],
        a['signature_address'],
        strip_armor(reviewer_text).replace('- ----', '-----')
    ]


def validate_audit(auditor_text):
    a = verify_sig(auditor_text)
    txt = strip_armor(auditor_text)
    ret = ""
    b = "- ----BEGIN BITCOIN SIGNED MESSAGE-----"
    c = "- ----BEGIN SIGNATURE-----"
    d = "- ----END BITCOIN SIGNED MESSAGE-----"
    for line in txt.splitlines():
        if line == b and ret.count(b[2:]) == 0:
            line = line.replace('- ----', '-----')
        elif line == c and ret.count(c[2:]) == 1:
            line = line.replace('- ----', '-----')
        elif line == d and ret.count(d[2:]) == 1:
            line = line.replace('- ----', '-----')
        ret += line + '\n'
    return [
        a['valid'],
        a['signature_address'],
        ret
    ]


if __name__ == "__main__":
    # enrollment sig
    sig1 = """-----BEGIN BITCOIN SIGNED MESSAGE-----
Name/handle: Test Person
Contact: tester@example.com
Master signing address: 1CptxARjqcfkVwGFSjR82zmPT8YtRMubub
Delegate signing address: 1Djp4Siv5iLJUgXq5peWCDcHVWV1Mv3opc
-----BEGIN SIGNATURE-----
1CptxARjqcfkVwGFSjR82zmPT8YtRMubub
H59sadjpiAgK6LaoiLEuZ3sSoFo6S2dSIjmETszVRGI6lccEgCaEgy7na1waF8TxHiVrV6qjha3m2Ih6ynAvGps=
-----END BITCOIN SIGNED MESSAGE-----"""
    # review sig
    sig2 = """-----BEGIN BITCOIN SIGNED MESSAGE-----
- ----BEGIN BITCOIN SIGNED MESSAGE-----
Name/Handle: Knightdk
Contact: knightdk on Bitcointalk.org
Master signing address: 16mT7jrpkjnJBD7a3TM2awyxHub58H6r6Z
Delegate signing address: N/A
Willing to mediate: Y
Mediation public key: 04594f2582c859c4f65084ee7fe8e9ec2d695bb988a3f53db48eaaff6ff3a0282b2be0c79"""\
"""fefca01277404d0fdc3a923e8ed02efd6ab96980f3e229a81fbe032e9
- ----BEGIN SIGNATURE-----
16mT7jrpkjnJBD7a3TM2awyxHub58H6r6Z
GxHE6iJH2aMpsRk7cszvXsLieDawzArpt7XDdOPhVFD5KVqIvKve1fwUKeN6ct4bld41XLdrZ7Dvaj7x1Oiw0uo=
- ----END BITCOIN SIGNED MESSAGE-----
-----BEGIN SIGNATURE-----
1BbgnPQYeXAt39ifLNUWP1RBktpzGLmRZS
IGJcg+MoqpBQNtptelyZfC2zBKk5SZQQjtf4pHSxb0yZH6kn/9Dhd1TWFfXUXsWmZq78xYye4lKi1aQUeNQ2ZFs=
-----END BITCOIN SIGNED MESSAGE-----"""
    # audit sig
    sig3 = """-----BEGIN BITCOIN SIGNED MESSAGE-----
- ----BEGIN BITCOIN SIGNED MESSAGE-----
- ----BEGIN BITCOIN SIGNED MESSAGE-----
Name/Handle: Knightdk
Contact: knightdk on Bitcointalk.org
Master signing address: 16mT7jrpkjnJBD7a3TM2awyxHub58H6r6Z
Delegate signing address: N/A
Willing to mediate: Y
Mediation public key: 04594f2582c859c4f65084ee7fe8e9ec2d695bb988a3f53db48eaaff6ff3a0282b2be0c79"""\
"""fefca01277404d0fdc3a923e8ed02efd6ab96980f3e229a81fbe032e9
- ----BEGIN SIGNATURE-----
16mT7jrpkjnJBD7a3TM2awyxHub58H6r6Z
GxHE6iJH2aMpsRk7cszvXsLieDawzArpt7XDdOPhVFD5KVqIvKve1fwUKeN6ct4bld41XLdrZ7Dvaj7x1Oiw0uo=
- ----END BITCOIN SIGNED MESSAGE-----
- ----BEGIN SIGNATURE-----
1BbgnPQYeXAt39ifLNUWP1RBktpzGLmRZS
IGJcg+MoqpBQNtptelyZfC2zBKk5SZQQjtf4pHSxb0yZH6kn/9Dhd1TWFfXUXsWmZq78xYye4lKi1aQUeNQ2ZFs=
- ----END BITCOIN SIGNED MESSAGE-----
-----BEGIN SIGNATURE-----
1DVK9Rdi2wcpcfEkep7FSNUui7fzadmxsW
IMcU7MvLl7T+hY0mmMw6mblLstnXd9Ly36z7uYMqv7ZZEuZQOvuXN2GjYU0Nq4So9GKQRkQwIis7EiN6luTMcOY=
-----END BITCOIN SIGNED MESSAGE-----"""
    sig4 = """-----BEGIN BITCOIN SIGNED MESSAGE-----
Rein Job
Job name: Record a Rein Theme Song
Job ID: n0qlna45dgynluhtl0gf
Category: Music
Description: Rein needs a theme song and what better way to do it than through Rein itself? This is call to any poet/musicians out there that are willing to rewrite some of the lyrics to Who'll Stop the Rain or I Can See Clearly Now to be about the following: decentralized money, gigs, Bitcoin, freelancing, Internet. The delivery should be a royalty-free song that you recorded yourself provided in FLAC or OGG format.
Mediator: bitspill
Mediator contact: rein@bitspill.net
Mediator fee: 1.0%
Mediator public key: 025a19c3aa027e9114a2f79b6e2f76d85c5b3fb59723ea9dba97e019f1f751d0eb
Mediator master address: 12AR5UHwzpqScrYiJvgJeNbp222eWv6vRF
Job creator: weex
Job creator contact: rein@exchb.com
Job creator public key: 0300d36a92be6d3c0b0d0aff5b14486b28013152acc4b55fc0ec84c4a209a3f234
Job creator master address: 1wexCMzikX7yxcWwzmbKHrtmxShqfthpe
-----BEGIN SIGNATURE-----
1dezq4MkERrT5vRKyMTLsugNz74JVy4U6
HwZeP2VhGu5VVFpfKLvSY2Zja/DPeax/2Lk0q3OnxujfKhWL/ex2fdyRosdBupTaQ7FxKl/NQP8+f55dt4PWUow=
-----END BITCOIN SIGNED MESSAGE-----"""
    # Test all of the functions
    print(validate_enrollment(sig1))
    print(validate_review(sig2))
    print(validate_audit(sig3))

    # Passing the output through all of the functions
    print(validate_enrollment(validate_review(validate_audit(sig3)[2])[2])['valid'])
    print(validate_enrollment(validate_review(sig2)[2])['valid'])
    print(validate_enrollment(sig1)['valid'])

    print('Description' in parse_document(sig4))
