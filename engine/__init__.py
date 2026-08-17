__all__ = []

# The v2-shadow branch is isolated from V1 production. Install the Tencent full-market
# cross-sectional fallback before engine.real_market is used by the V2 snapshot pipeline.
# This affects only AKShareMarket.snapshot(); execution prices still use daily-bar methods.
try:
    from engine_v2.tencent_full_market import install as _install_v2_tencent_full_market
    _install_v2_tencent_full_market()
except Exception as _v2_market_patch_error:
    # Do not break package import. The normal provider path will surface a real production
    # failure later if the fallback cannot be installed.
    print(f'[V2] Tencent full-market fallback install warning: {_v2_market_patch_error}')
