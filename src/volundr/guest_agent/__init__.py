"""The VM guest metadata agent.

Runs inside the Skuld image on a VM guest as ``python -m volundr.guest_agent
{once|serve} --config PATH``. It fetches the session's credentials from Forge's
metadata endpoint, writes them as files, and (``serve``) keeps them fresh and
answers the guest's own processes on the link-local metadata address.

Nothing is imported here: the agent stays light and starts fast.
"""
