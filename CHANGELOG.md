# Changelog

## [0.2.1](https://github.com/klconsultancy/ha-peak-basket/compare/v0.2.0...v0.2.1) (2026-11-28)


### Bug Fixes

* report sensors write at most every 30 s and at the window's edges ([#95](https://github.com/klconsultancy/ha-peak-basket/issues/95)) ([#96](https://github.com/klconsultancy/ha-peak-basket/issues/96)) ([b000c2e](https://github.com/klconsultancy/ha-peak-basket/commit/b000c2ebab4b2ba648844510b14d4034408461f5))

## [0.2.0](https://github.com/klconsultancy/ha-peak-basket/compare/v0.1.0...v0.2.0) (2026-11-23)


### ⚠ BREAKING CHANGES

* sensor names change. Existing sensors keep their entity IDs; the new IDs (such as `sensor.peak_basket_ev_charger_allowed_power`) apply to new installs and new loads, so rename by hand in Settings → Entities to use them, and update dashboards and EVCC. Reserve now means the safety margin in energy: 0.1.0's house reserve is now Expected import.

### Features

* a load that is only measured, to see its share of the basket ([#74](https://github.com/klconsultancy/ha-peak-basket/issues/74)) ([4193787](https://github.com/klconsultancy/ha-peak-basket/commit/4193787333b7116a53b50a8fa55e70f669a6f664))
* a load's device links to the appliance it measures and takes its area ([#81](https://github.com/klconsultancy/ha-peak-basket/issues/81)) ([55d063d](https://github.com/klconsultancy/ha-peak-basket/commit/55d063dc34264845280b395f376c09032bfe74de))
* one language for names, one entity-ID convention, and a power sensor per load ([#80](https://github.com/klconsultancy/ha-peak-basket/issues/80)) ([4fb4379](https://github.com/klconsultancy/ha-peak-basket/commit/4fb43793c7ce22f9b9feb1afb6b80419c428d852))


### Bug Fixes

* a cycling house appliance no longer flips the mode ([#92](https://github.com/klconsultancy/ha-peak-basket/issues/92)) ([#94](https://github.com/klconsultancy/ha-peak-basket/issues/94)) ([64ca8da](https://github.com/klconsultancy/ha-peak-basket/commit/64ca8da17514ad6097cbd6f413ab49ee3933b257))
* a load sensor that lags the grid power no longer reads as a house step ([#85](https://github.com/klconsultancy/ha-peak-basket/issues/85)) ([be074d9](https://github.com/klconsultancy/ha-peak-basket/commit/be074d9f53f39bccd6a08411d2945dafce77a6c4))
* continuous never offers a load less than pulse on ([#91](https://github.com/klconsultancy/ha-peak-basket/issues/91)) ([#93](https://github.com/klconsultancy/ha-peak-basket/issues/93)) ([0db0c59](https://github.com/klconsultancy/ha-peak-basket/commit/0db0c5961d81a1e50ea3bd7d81afea3f716a7965))
* replace the deprecated device_registry.async_get_device lookup ([#84](https://github.com/klconsultancy/ha-peak-basket/issues/84)) ([296a8fb](https://github.com/klconsultancy/ha-peak-basket/commit/296a8fbd774b8f77f4f03bfe5d5c30aae358d2c3))
* the mode does not flip around an idle load's start ([#90](https://github.com/klconsultancy/ha-peak-basket/issues/90)) ([cb180e2](https://github.com/klconsultancy/ha-peak-basket/commit/cb180e2f14ffd86b41814a799be6cb36fde232a3))
* the mode does not flip on every evaluation around the continuous boundary ([#86](https://github.com/klconsultancy/ha-peak-basket/issues/86)) ([88ce71e](https://github.com/klconsultancy/ha-peak-basket/commit/88ce71ee1d35f8afa0ad7aaa97eb0842210f9fd1))
* the Wants power help text no longer says it only matters with more than one load ([#89](https://github.com/klconsultancy/ha-peak-basket/issues/89)) ([6aa87e2](https://github.com/klconsultancy/ha-peak-basket/commit/6aa87e2b78cf0de03fbabb6563aaa80dee56cf8a))

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
