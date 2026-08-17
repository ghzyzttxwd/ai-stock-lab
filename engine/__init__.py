__all__ = []

# V1 uses Eastmoney/Sina first and Tencent full-market as the third complete
# cross-sectional source. The installer only wraps market loading; broker/execution
# rules and V1 ledger semantics remain unchanged.
try:
    from .tencent_full_market import install as _install_v1_tencent_full_market
    _install_v1_tencent_full_market()
except Exception as _v1_market_patch_error:
    # Do not make package import fatal. If the fallback cannot be installed, the
    # production market path will fail closed rather than fabricating data.
    print(f'[V1] Tencent full-market fallback install warning: {_v1_market_patch_error}')
