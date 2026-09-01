# Changelog

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
