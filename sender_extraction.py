"""Attach source-grounded sender identities to existing DOM scans."""

from pathlib import Path

ACCOUNT = Path(__file__).with_name("account_identity.js").read_text(encoding="utf8")
RESOLVE = Path(__file__).with_name("sender_identity.js").read_text(encoding="utf8")


def wrap(scan, provider=None):
    provider_js = repr(provider) if provider else "args.provider"
    return f"""async args => {{
      const provider = {provider_js};
      const currentAccount = await ({ACCOUNT})(provider);
      const previousAccount = window.__backupAccountIdentity;
      if (previousAccount?.id && currentAccount.id && previousAccount.id !== currentAccount.id) throw new Error('Messenger account changed. Capture stopped.');
      window.__backupAccountIdentity = currentAccount;
      const rows = ({scan})(args);
      const resolveSenders = ({RESOLVE});
      return resolveSenders(rows, {{selector: typeof args === 'string' ? args : args.selector, provider: {provider_js}}});
    }}"""
