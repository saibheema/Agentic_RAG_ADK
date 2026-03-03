"""
Enable OpenSSL 3.0 legacy provider in /etc/ssl/openssl.cnf
so strongSwan's openssl plugin can use 3DES_CBC (required by Youngsinc VPN).
"""
import re
import os

cfg = '/etc/ssl/openssl.cnf'

# Minimal config that enables legacy provider — will be created if not present
LEGACY_CONFIG = """\nopenssl_conf = openssl_init\n\n[openssl_init]\nproviders = provider_sect\n\n[provider_sect]\ndefault = default_sect\nlegacy = legacy_sect\n\n[default_sect]\nactivate = 1\n\n[legacy_sect]\nactivate = 1\n"""

if not os.path.exists(cfg):
    with open(cfg, 'w') as f:
        f.write(LEGACY_CONFIG)
    print(f'Created {cfg} with legacy provider enabled')
else:
    with open(cfg, 'r') as f:
        content = f.read()

    # Remove any existing (possibly broken) openssl_conf declaration and append a clean one
    # Strip out existing openssl_conf = ... lines and our previously-added provider sections
    content = re.sub(r'^openssl_conf\s*=.*\n?', '', content, flags=re.MULTILINE)
    content = re.sub(r'\[openssl_init\][^\[]*', '', content, flags=re.DOTALL)
    content = re.sub(r'\[provider_sect\][^\[]*', '', content, flags=re.DOTALL)
    content = re.sub(r'\[default_sect\][^\[]*', '', content, flags=re.DOTALL)
    content = re.sub(r'\[legacy_sect\][^\[]*', '', content, flags=re.DOTALL)

    # Prepend the clean provider config
    content = LEGACY_CONFIG + '\n' + content

    with open(cfg, 'w') as f:
        f.write(content)
    print(f'Rewrote {cfg}: OpenSSL legacy provider enabled for 3DES_CBC support')
