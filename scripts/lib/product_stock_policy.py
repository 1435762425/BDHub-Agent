"""User-confirmed inventory policy; full-managed identity needs official evidence."""
def full_managed(offer):
    return offer.get('managementType')=='full_managed' and isinstance(offer.get('managementEvidenceRef'),str) and bool(offer['managementEvidenceRef'])

def require_stock(offer):return not full_managed(offer)

def unavailable_allowed(value,offer):
    # Platform code 8 is temporary stock absence, not delisting/governance.
    allowed=(None,'',0,'0','0.0')+((8,'8','8.0') if full_managed(offer) else ())
    return value in allowed

def mark_full_managed(offer,evidence):
    return {**offer,'managementType':'full_managed','managementEvidenceRef':evidence,'stock':None,'stockRequired':False}
