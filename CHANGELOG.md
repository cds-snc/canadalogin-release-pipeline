# Changelog

## [1.2.2](https://github.com/cds-snc/canadalogin-release-system/compare/v1.2.1...v1.2.2) (2026-09-03)


### Bug Fixes

* bound notification capture retention ([#71](https://github.com/cds-snc/canadalogin-release-system/issues/71)) ([27cf734](https://github.com/cds-snc/canadalogin-release-system/commit/27cf734a2eba5a447beb3cff325b3aec750cc871))
* restore release workflow setup ([#72](https://github.com/cds-snc/canadalogin-release-system/issues/72)) ([92d8125](https://github.com/cds-snc/canadalogin-release-system/commit/92d8125cb55d83af2ae40667d0c8bf913847dc24))


### Code Refactoring

* remove legacy module shims ([#69](https://github.com/cds-snc/canadalogin-release-system/issues/69)) ([d624061](https://github.com/cds-snc/canadalogin-release-system/commit/d624061652f2b28c133e832093c2dbee126236a1))

## [1.2.1](https://github.com/cds-snc/canadalogin-release-system/compare/v1.2.0...v1.2.1) (2026-09-03)


### Bug Fixes

* acceptance reusable workflow failures ([#54](https://github.com/cds-snc/canadalogin-release-system/issues/54)) ([b1ce3e1](https://github.com/cds-snc/canadalogin-release-system/commit/b1ce3e16267e751c1db037bb2c817434c19039c7))
* allow acceptance Lambda provisioning ([#55](https://github.com/cds-snc/canadalogin-release-system/issues/55)) ([2555c64](https://github.com/cds-snc/canadalogin-release-system/commit/2555c641dac1afd8ef59f1c1380596016a3d261a))
* allow notification capture function URL invocations ([#60](https://github.com/cds-snc/canadalogin-release-system/issues/60)) ([dd51688](https://github.com/cds-snc/canadalogin-release-system/commit/dd516886b84f6447af38bbd6655f297253ea427a))
* correct Lambda URL invoke permission ([#63](https://github.com/cds-snc/canadalogin-release-system/issues/63)) ([1be0d06](https://github.com/cds-snc/canadalogin-release-system/commit/1be0d0639cf0aab5869a1d928aa837b71d4c99d8))
* deploy versioned environments on promotion ([#51](https://github.com/cds-snc/canadalogin-release-system/issues/51)) ([f6ff396](https://github.com/cds-snc/canadalogin-release-system/commit/f6ff3962943f98dd8ce9860c91bbd673bfb47f7e))
* Fix planner interface output ([#52](https://github.com/cds-snc/canadalogin-release-system/issues/52)) ([97d44af](https://github.com/cds-snc/canadalogin-release-system/commit/97d44af0187bf7f6d52e62269a62eab5ccc21eea))
* format deployment plan matrix logs ([#49](https://github.com/cds-snc/canadalogin-release-system/issues/49)) ([d32658b](https://github.com/cds-snc/canadalogin-release-system/commit/d32658b22424d87ee22a4db605bfb5ade2c975b8))
* format plan matrix logs ([d32658b](https://github.com/cds-snc/canadalogin-release-system/commit/d32658b22424d87ee22a4db605bfb5ade2c975b8))
* shorten acceptance fixture resource names ([#58](https://github.com/cds-snc/canadalogin-release-system/issues/58)) ([e9a1df3](https://github.com/cds-snc/canadalogin-release-system/commit/e9a1df3443c4aee5684af94b995ed0ac1b4d4c16))
* skip SBOM after build failure ([#64](https://github.com/cds-snc/canadalogin-release-system/issues/64)) ([13bbf6e](https://github.com/cds-snc/canadalogin-release-system/commit/13bbf6ee1dcd34c60827189819c7e75ca4a1ba9d))
* suppress acceptance deployment records ([#62](https://github.com/cds-snc/canadalogin-release-system/issues/62)) ([5f5dc24](https://github.com/cds-snc/canadalogin-release-system/commit/5f5dc24064ff4bb0c0d15ac443b6c6d890fd3c7a))
* validate acceptance Terraform in CI ([#61](https://github.com/cds-snc/canadalogin-release-system/issues/61)) ([4bcf8e2](https://github.com/cds-snc/canadalogin-release-system/commit/4bcf8e27a0d562f774c05125ead17f6be61da4ec))
* verify absent image after failed build ([#66](https://github.com/cds-snc/canadalogin-release-system/issues/66)) ([8e4130a](https://github.com/cds-snc/canadalogin-release-system/commit/8e4130a1a68d754653096d9b10c3c7686f6cfc4c))
* verify deploy failure alert text ([716dbf4](https://github.com/cds-snc/canadalogin-release-system/commit/716dbf41e6a3a1b3e4fa1ec1344db2b8b3679a53))
* verify deploy failure Slack alert ([#68](https://github.com/cds-snc/canadalogin-release-system/issues/68)) ([716dbf4](https://github.com/cds-snc/canadalogin-release-system/commit/716dbf41e6a3a1b3e4fa1ec1344db2b8b3679a53))


### Code Refactoring

* namespace release modules ([#67](https://github.com/cds-snc/canadalogin-release-system/issues/67)) ([f48afac](https://github.com/cds-snc/canadalogin-release-system/commit/f48afacee41493d8221db95f8cba1e0be417aa8d))
* simplify acceptance workflows ([#56](https://github.com/cds-snc/canadalogin-release-system/issues/56)) ([b2cc93f](https://github.com/cds-snc/canadalogin-release-system/commit/b2cc93f24dbe0025f134b93c8dc04ac64bbab6eb))


### Tests

* cover release build and deploy failures ([#53](https://github.com/cds-snc/canadalogin-release-system/issues/53)) ([0ae550f](https://github.com/cds-snc/canadalogin-release-system/commit/0ae550f898ad0952d61240241e4690e69dec302b))


### Miscellaneous Chores

* rename acceptance workflows ([#59](https://github.com/cds-snc/canadalogin-release-system/issues/59)) ([ea33a84](https://github.com/cds-snc/canadalogin-release-system/commit/ea33a84fd6039752e454c44fecf3e37999fee438))


### Documentation

* Workflow docs ([#65](https://github.com/cds-snc/canadalogin-release-system/issues/65)) ([a84bc18](https://github.com/cds-snc/canadalogin-release-system/commit/a84bc184127e604d5c5058332fb48cd759e1e629))

## [1.2.0](https://github.com/cds-snc/canadalogin-release-system/compare/v1.1.0...v1.2.0) (2026-09-01)


### Features

* requirements.lock ([#39](https://github.com/cds-snc/canadalogin-release-system/issues/39)) ([e0fda9d](https://github.com/cds-snc/canadalogin-release-system/commit/e0fda9d7de4abe162de00d3342bfd38eb97bb490))


### Bug Fixes

* reject unconfigured repository dispatch events ([#42](https://github.com/cds-snc/canadalogin-release-system/issues/42)) ([08c0503](https://github.com/cds-snc/canadalogin-release-system/commit/08c0503f8231b36c5624a78fe0de7a776620eb8a))
* remove obsolete acceptance health check fields ([#48](https://github.com/cds-snc/canadalogin-release-system/issues/48)) ([ae9fa7b](https://github.com/cds-snc/canadalogin-release-system/commit/ae9fa7bcf7a671d0f130c8a1e60b5515a85e8e82))


### Code Refactoring

* isolate acceptance workflow overrides ([#43](https://github.com/cds-snc/canadalogin-release-system/issues/43)) ([dd9e2ec](https://github.com/cds-snc/canadalogin-release-system/commit/dd9e2eccdaf1ce75a710ffaa1a10e5bb4a7d52a4))
* make load-test builds required ([#37](https://github.com/cds-snc/canadalogin-release-system/issues/37)) ([72aef24](https://github.com/cds-snc/canadalogin-release-system/commit/72aef242c37f266bcbdf8c3e4c60bb95814a34aa))
* remove lifecycle hooks ([#44](https://github.com/cds-snc/canadalogin-release-system/issues/44)) ([43bc8bd](https://github.com/cds-snc/canadalogin-release-system/commit/43bc8bd67254338018cd795eb02a53d7e252e4a8))
* remove schema secret references ([#41](https://github.com/cds-snc/canadalogin-release-system/issues/41)) ([faa4b15](https://github.com/cds-snc/canadalogin-release-system/commit/faa4b15720efb8082fa65b05871d9cada873ac25))
* require load-test builds ([72aef24](https://github.com/cds-snc/canadalogin-release-system/commit/72aef242c37f266bcbdf8c3e4c60bb95814a34aa))

## [1.1.0](https://github.com/cds-snc/canadalogin-release-system/compare/v1.0.14...v1.1.0) (2026-08-28)


### Features

* Clean up release pipeline logging ([#29](https://github.com/cds-snc/canadalogin-release-system/issues/29)) ([2be0857](https://github.com/cds-snc/canadalogin-release-system/commit/2be08575fe3b0c905b909b52aa29924cc1c243a2))
* remove max-parallel and fail-fast, they offer no needed functionality and slow deployments ([#27](https://github.com/cds-snc/canadalogin-release-system/issues/27)) ([458e453](https://github.com/cds-snc/canadalogin-release-system/commit/458e453f5853c92303a15406250ef29b07a6dd60))


### Bug Fixes

* avoid redundant acceptance IAM policy updates ([#30](https://github.com/cds-snc/canadalogin-release-system/issues/30)) ([198a36b](https://github.com/cds-snc/canadalogin-release-system/commit/198a36b9545c5753befb13608828a291eb7f55cc))
* improve release pipeline rollout logging ([#32](https://github.com/cds-snc/canadalogin-release-system/issues/32)) ([ce79220](https://github.com/cds-snc/canadalogin-release-system/commit/ce79220f206403d72d1c6d71941b38f96041b67e))
* isolate build artifacts by environment ([#34](https://github.com/cds-snc/canadalogin-release-system/issues/34)) ([09da0be](https://github.com/cds-snc/canadalogin-release-system/commit/09da0be6e909a81181e6d7e4d01471a6f900bdb1))
* verify environment-qualified React artifacts ([#35](https://github.com/cds-snc/canadalogin-release-system/issues/35)) ([8a74d4f](https://github.com/cds-snc/canadalogin-release-system/commit/8a74d4fc66d06a286311c92014fd22da2c4c43a3))


### Code Refactoring

* extract CLI action handlers ([#36](https://github.com/cds-snc/canadalogin-release-system/issues/36)) ([5d317ec](https://github.com/cds-snc/canadalogin-release-system/commit/5d317ec956cb5c894a1e0e8f8e6a8ecbf63e1168))


### Continuous Integration

* add PR title check ([#31](https://github.com/cds-snc/canadalogin-release-system/issues/31)) ([d899fde](https://github.com/cds-snc/canadalogin-release-system/commit/d899fde241212a1c3914d033569fdb334309e207))


### Documentation

* consolidate schema reference ([#33](https://github.com/cds-snc/canadalogin-release-system/issues/33)) ([3bc38ce](https://github.com/cds-snc/canadalogin-release-system/commit/3bc38ced39797ccf758862d30d82a4350c59c84e))

## [1.0.14](https://github.com/cds-snc/canadalogin-release-system/compare/v1.0.13...v1.0.14) (2026-08-28)


### Bug Fixes

* align acceptance verifier SSM paths ([#24](https://github.com/cds-snc/canadalogin-release-system/issues/24)) ([46b7dcb](https://github.com/cds-snc/canadalogin-release-system/commit/46b7dcb98e099f2ed1b2a4f27ddb6b1f2d3fb5ed))
* **ci:** trigger mergeability check after acceptance ([33905dd](https://github.com/cds-snc/canadalogin-release-system/commit/33905dd95010a0be2a88dc9c50c91a2287a074d8))
* **ci:** trigger mergeability check after acceptance ([ebdbe15](https://github.com/cds-snc/canadalogin-release-system/commit/ebdbe1516394cb51296a4361da4b835428636286))
* don't forget the conventional commit prefix ([#22](https://github.com/cds-snc/canadalogin-release-system/issues/22)) ([d416a18](https://github.com/cds-snc/canadalogin-release-system/commit/d416a1871616589562e42911fc67466173bf2bed))
* Fix ssm paths ([#23](https://github.com/cds-snc/canadalogin-release-system/issues/23)) ([07a4697](https://github.com/cds-snc/canadalogin-release-system/commit/07a469730fc609bfc1cec163c66a047173d6aef7))
* handle expected acceptance failures ([8c329e5](https://github.com/cds-snc/canadalogin-release-system/commit/8c329e5027d7e32d13bf001c941493e8ed001941))
* handle expected acceptance failures ([92de822](https://github.com/cds-snc/canadalogin-release-system/commit/92de822a20b5b986d2b50252d20b745f1854c96f))
* rename check ([#26](https://github.com/cds-snc/canadalogin-release-system/issues/26)) ([657e5d0](https://github.com/cds-snc/canadalogin-release-system/commit/657e5d0dcc29281f035db0f371bc9a13a56d6c14))
* update release gate on acceptance status ([#25](https://github.com/cds-snc/canadalogin-release-system/issues/25)) ([2fdedec](https://github.com/cds-snc/canadalogin-release-system/commit/2fdedeca883d612b4147efd5583aeeffd26cfce8))


### Continuous Integration

* automate acceptance Terraform setup ([#18](https://github.com/cds-snc/canadalogin-release-system/issues/18)) ([500b5c4](https://github.com/cds-snc/canadalogin-release-system/commit/500b5c4666346ebbfec401190ebbf9eecd5e3ecc))
* organize acceptance tests by package ([#19](https://github.com/cds-snc/canadalogin-release-system/issues/19)) ([f81d471](https://github.com/cds-snc/canadalogin-release-system/commit/f81d471976606028d93f2c128b7752a57284f633))
* tighten release pipeline mergeability checks ([3a85afd](https://github.com/cds-snc/canadalogin-release-system/commit/3a85afd5c94b898f625c502f4538c16eaef65249))
* tighten release pipeline mergeability checks ([390f1e5](https://github.com/cds-snc/canadalogin-release-system/commit/390f1e561a9f83c0f7ca7ee9c63cdcc5b72605c6))

## [1.0.13](https://github.com/cds-snc/canadalogin-release-system/compare/v1.0.12...v1.0.13) (2026-08-25)


### Bug Fixes

* document release acceptance workflow ([869061e](https://github.com/cds-snc/canadalogin-release-system/commit/869061e26d1e637aff3a5a69df6d78faa8b9666e))
