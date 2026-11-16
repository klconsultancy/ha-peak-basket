# Changelog

## 0.1.0 (2026-11-16)


### Features

* add the Peak Basket integration ([81cd702](https://github.com/klconsultancy/ha-peak-basket/commit/81cd70245b5fbc5ac261012e2842d3f9c173778b))
* one entry per grid connection, with loads as subentries ([#14](https://github.com/klconsultancy/ha-peak-basket/issues/14)) ([36c5be6](https://github.com/klconsultancy/ha-peak-basket/commit/36c5be6ff7c9f7e4b0317b9455bd3ac204c5195e))
* reconfigure the grid connection's sources ([#45](https://github.com/klconsultancy/ha-peak-basket/issues/45)) ([23a4138](https://github.com/klconsultancy/ha-peak-basket/commit/23a4138c978b2e46ee9e2eabbe336488633410e9))
* show the window's solar and grid energy per load and for the house ([#17](https://github.com/klconsultancy/ha-peak-basket/issues/17)) ([1fca6ea](https://github.com/klconsultancy/ha-peak-basket/commit/1fca6ea009064b9df1500bd83154b16e3d97549a))
* split each window's import over the house and the loads, also without solar ([#23](https://github.com/klconsultancy/ha-peak-basket/issues/23)) ([7807d77](https://github.com/klconsultancy/ha-peak-basket/commit/7807d77dd1ef3c29543ccc0ac107274da2f1588b))


### Bug Fixes

* a whole-grid limit per load, and no double claim when loads start together ([#31](https://github.com/klconsultancy/ha-peak-basket/issues/31)) ([792f8d4](https://github.com/klconsultancy/ha-peak-basket/commit/792f8d41ae4d8f8bedca9a013b3588ce1e11b9b1))
* align windows to the local clock, also around DST and in half-hour zones ([#11](https://github.com/klconsultancy/ha-peak-basket/issues/11)) ([8dfed49](https://github.com/klconsultancy/ha-peak-basket/commit/8dfed4923ed5537d864b607e7771612773813cb0))
* an idle load claims its room only while it has demand ([#34](https://github.com/klconsultancy/ha-peak-basket/issues/34)) ([141baf9](https://github.com/klconsultancy/ha-peak-basket/commit/141baf93b984adcff958b9771f9d95ecf5611e65))
* cap the window's import at what is physically possible since the boundary ([#29](https://github.com/klconsultancy/ha-peak-basket/issues/29)) ([5fe7547](https://github.com/klconsultancy/ha-peak-basket/commit/5fe7547aea4d75b96b8866938e33d79387c399f6))
* clearer names in English and Dutch, and the house as its own device ([#49](https://github.com/klconsultancy/ha-peak-basket/issues/49)) ([ff78a9a](https://github.com/klconsultancy/ha-peak-basket/commit/ff78a9a2b2a5a8f937b7d544ae4b98cf394d8475))
* count a load's missing or stale power as 0 W, so it can start ([#28](https://github.com/klconsultancy/ha-peak-basket/issues/28)) ([8f2f5a8](https://github.com/klconsultancy/ha-peak-basket/commit/8f2f5a8288da9ff1472f7cd17b7697cbe0957a34))
* count a missing or stale PV power as 0 W, so the loads' grid energy keeps counting at night ([#44](https://github.com/klconsultancy/ha-peak-basket/issues/44)) ([1e9963c](https://github.com/klconsultancy/ha-peak-basket/commit/1e9963c1a70a9fd822829079a1d8804335c1373e))
* count the boundary interval, check the release zip, and say loads must follow their allowed power ([#35](https://github.com/klconsultancy/ha-peak-basket/issues/35)) ([4c07d8e](https://github.com/klconsultancy/ha-peak-basket/commit/4c07d8e96bd8fed7266bdba0535a45307ecff501))
* ignore the previous window's average right after the boundary ([#12](https://github.com/klconsultancy/ha-peak-basket/issues/12)) ([f88bf1e](https://github.com/klconsultancy/ha-peak-basket/commit/f88bf1ed15bcf803c429761a914d1e266ffd619b))
* keep the window's import when a meter ahead resets its average early ([#55](https://github.com/klconsultancy/ha-peak-basket/issues/55)) ([b93f611](https://github.com/klconsultancy/ha-peak-basket/commit/b93f611f4700fbc8de8645914ac6fb3da8f00d92))
* migrate only version 1 entries, and name the migrated load in the system language ([#26](https://github.com/klconsultancy/ha-peak-basket/issues/26)) ([0f208e2](https://github.com/klconsultancy/ha-peak-basket/commit/0f208e24a644fa02b538f32806d8977cad89ffcd))
* name the connection's device Grid connection, with language-independent entity IDs ([#27](https://github.com/klconsultancy/ha-peak-basket/issues/27)) ([41d63c6](https://github.com/klconsultancy/ha-peak-basket/commit/41d63c63de2a7aff61464cfb2a275bfd73e7cf75))
* read the window's counter start in its own unit after a restart ([#51](https://github.com/klconsultancy/ha-peak-basket/issues/51)) ([d3319b0](https://github.com/klconsultancy/ha-peak-basket/commit/d3319b0ac7bb5ad6b4ca132c2e42b6394aa26d11))
* show Peak Basket as a service integration, not a helper ([#10](https://github.com/klconsultancy/ha-peak-basket/issues/10)) ([be88c29](https://github.com/klconsultancy/ha-peak-basket/commit/be88c29c5e5cb80324a1508511918cdcc2af8007))
* stay unavailable after a restart until the meter reports, instead of 0 W ([#53](https://github.com/klconsultancy/ha-peak-basket/issues/53)) ([dc93770](https://github.com/klconsultancy/ha-peak-basket/commit/dc93770f2b403f37a03d85620fc531942b2d51fd))


### Performance Improvements

* write far fewer sensor states to the recorder ([#13](https://github.com/klconsultancy/ha-peak-basket/issues/13)) ([f09bbb0](https://github.com/klconsultancy/ha-peak-basket/commit/f09bbb0e77f527c8952088ec71ec87cababbd382))
