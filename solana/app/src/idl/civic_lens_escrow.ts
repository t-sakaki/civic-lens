/**
 * Program IDL in camelCase format in order to be used in JS/TS.
 *
 * Note that this is only a type helper and is not the actual IDL. The original
 * IDL can be found at `target/idl/civic_lens_escrow.json`.
 */
export type CivicLensEscrow = {
  "address": "Ca7tu8tfHfJt4TgEzcPxKWtYiYUCigvWh6tr78Q7TXQ4",
  "metadata": {
    "name": "civicLensEscrow",
    "version": "0.1.0",
    "spec": "0.1.0",
    "description": "Civic Lens: 行政不服審査ケースの支援金エスクロー（総務省裁決DBオラクル）"
  },
  "instructions": [
    {
      "name": "arbitrate",
      "docs": [
        "異議時のみ arbiter が最終結論を決定する"
      ],
      "discriminator": [
        105,
        91,
        110,
        150,
        216,
        11,
        142,
        142
      ],
      "accounts": [
        {
          "name": "config",
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  111,
                  110,
                  102,
                  105,
                  103
                ]
              }
            ]
          }
        },
        {
          "name": "case",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  97,
                  115,
                  101
                ]
              },
              {
                "kind": "account",
                "path": "case.id",
                "account": "case"
              }
            ]
          }
        },
        {
          "name": "arbiter",
          "signer": true,
          "relations": [
            "config"
          ]
        }
      ],
      "args": [
        {
          "name": "outcome",
          "type": {
            "defined": {
              "name": "outcome"
            }
          }
        },
        {
          "name": "sourceHash",
          "type": {
            "array": [
              "u8",
              32
            ]
          }
        }
      ]
    },
    {
      "name": "challenge",
      "docs": [
        "支援者（Pledge保有者）のみ。異議申立期間内に限る。"
      ],
      "discriminator": [
        16,
        107,
        14,
        39,
        244,
        150,
        81,
        187
      ],
      "accounts": [
        {
          "name": "case",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  97,
                  115,
                  101
                ]
              },
              {
                "kind": "account",
                "path": "case.id",
                "account": "case"
              }
            ]
          },
          "relations": [
            "pledge"
          ]
        },
        {
          "name": "pledge",
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  112,
                  108,
                  101,
                  100,
                  103,
                  101
                ]
              },
              {
                "kind": "account",
                "path": "case"
              },
              {
                "kind": "account",
                "path": "supporter"
              }
            ]
          }
        },
        {
          "name": "supporter",
          "signer": true,
          "relations": [
            "pledge"
          ]
        }
      ],
      "args": []
    },
    {
      "name": "createCase",
      "docs": [
        "`beneficiary` は申立人本人（= creator）か、有効な `Professional` 登録のあるウォレットのみ。",
        "`review_key_hash`: sha256(審査庁 | 処分の種類 | 申立日) — DB掲載裁決との突合キー。",
        "個人情報はオンチェーンに載せず、証拠もハッシュ＋削除可能なストレージのURIのみ。"
      ],
      "discriminator": [
        38,
        206,
        153,
        185,
        128,
        192,
        221,
        84
      ],
      "accounts": [
        {
          "name": "config",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  111,
                  110,
                  102,
                  105,
                  103
                ]
              }
            ]
          }
        },
        {
          "name": "case",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  97,
                  115,
                  101
                ]
              },
              {
                "kind": "account",
                "path": "config.caseCount",
                "account": "config"
              }
            ]
          }
        },
        {
          "name": "caseVault",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "account",
                "path": "case"
              },
              {
                "kind": "account",
                "path": "tokenProgram"
              },
              {
                "kind": "account",
                "path": "usdcMint"
              }
            ],
            "program": {
              "kind": "const",
              "value": [
                140,
                151,
                37,
                143,
                78,
                36,
                137,
                241,
                187,
                61,
                16,
                41,
                20,
                142,
                13,
                131,
                11,
                90,
                19,
                153,
                218,
                255,
                16,
                132,
                4,
                142,
                123,
                216,
                219,
                233,
                248,
                89
              ]
            }
          }
        },
        {
          "name": "usdcMint"
        },
        {
          "name": "professional",
          "docs": [
            "beneficiary が creator 本人でない場合のみ必須"
          ],
          "optional": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  112,
                  114,
                  111,
                  102,
                  101,
                  115,
                  115,
                  105,
                  111,
                  110,
                  97,
                  108
                ]
              },
              {
                "kind": "arg",
                "path": "beneficiary"
              }
            ]
          }
        },
        {
          "name": "creator",
          "writable": true,
          "signer": true
        },
        {
          "name": "tokenProgram"
        },
        {
          "name": "associatedTokenProgram",
          "address": "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
        },
        {
          "name": "systemProgram",
          "address": "11111111111111111111111111111111"
        }
      ],
      "args": [
        {
          "name": "title",
          "type": "string"
        },
        {
          "name": "evidenceHash",
          "type": {
            "array": [
              "u8",
              32
            ]
          }
        },
        {
          "name": "evidenceUri",
          "type": "string"
        },
        {
          "name": "reviewKeyHash",
          "type": {
            "array": [
              "u8",
              32
            ]
          }
        },
        {
          "name": "beneficiary",
          "type": "pubkey"
        },
        {
          "name": "deadline",
          "type": "i64"
        }
      ]
    },
    {
      "name": "finalize",
      "docs": [
        "異議なしで期間経過 → 誰でも確定可（パーミッションレスなcrank）"
      ],
      "discriminator": [
        171,
        61,
        218,
        56,
        127,
        115,
        12,
        217
      ],
      "accounts": [
        {
          "name": "case",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  97,
                  115,
                  101
                ]
              },
              {
                "kind": "account",
                "path": "case.id",
                "account": "case"
              }
            ]
          }
        }
      ],
      "args": []
    },
    {
      "name": "initialize",
      "discriminator": [
        175,
        175,
        109,
        31,
        13,
        152,
        155,
        237
      ],
      "accounts": [
        {
          "name": "config",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  111,
                  110,
                  102,
                  105,
                  103
                ]
              }
            ]
          }
        },
        {
          "name": "usdcMint"
        },
        {
          "name": "admin",
          "writable": true,
          "signer": true
        },
        {
          "name": "systemProgram",
          "address": "11111111111111111111111111111111"
        }
      ],
      "args": [
        {
          "name": "agent",
          "type": "pubkey"
        },
        {
          "name": "arbiter",
          "type": "pubkey"
        }
      ]
    },
    {
      "name": "pledgeSol",
      "discriminator": [
        13,
        126,
        236,
        244,
        55,
        207,
        124,
        23
      ],
      "accounts": [
        {
          "name": "case",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  97,
                  115,
                  101
                ]
              },
              {
                "kind": "account",
                "path": "case.id",
                "account": "case"
              }
            ]
          }
        },
        {
          "name": "pledge",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  112,
                  108,
                  101,
                  100,
                  103,
                  101
                ]
              },
              {
                "kind": "account",
                "path": "case"
              },
              {
                "kind": "account",
                "path": "supporter"
              }
            ]
          }
        },
        {
          "name": "supporter",
          "writable": true,
          "signer": true
        },
        {
          "name": "systemProgram",
          "address": "11111111111111111111111111111111"
        }
      ],
      "args": [
        {
          "name": "amount",
          "type": "u64"
        }
      ]
    },
    {
      "name": "pledgeUsdc",
      "discriminator": [
        247,
        111,
        44,
        8,
        180,
        129,
        202,
        223
      ],
      "accounts": [
        {
          "name": "config",
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  111,
                  110,
                  102,
                  105,
                  103
                ]
              }
            ]
          }
        },
        {
          "name": "case",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  97,
                  115,
                  101
                ]
              },
              {
                "kind": "account",
                "path": "case.id",
                "account": "case"
              }
            ]
          }
        },
        {
          "name": "pledge",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  112,
                  108,
                  101,
                  100,
                  103,
                  101
                ]
              },
              {
                "kind": "account",
                "path": "case"
              },
              {
                "kind": "account",
                "path": "supporter"
              }
            ]
          }
        },
        {
          "name": "caseVault",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "account",
                "path": "case"
              },
              {
                "kind": "account",
                "path": "tokenProgram"
              },
              {
                "kind": "account",
                "path": "usdcMint"
              }
            ],
            "program": {
              "kind": "const",
              "value": [
                140,
                151,
                37,
                143,
                78,
                36,
                137,
                241,
                187,
                61,
                16,
                41,
                20,
                142,
                13,
                131,
                11,
                90,
                19,
                153,
                218,
                255,
                16,
                132,
                4,
                142,
                123,
                216,
                219,
                233,
                248,
                89
              ]
            }
          }
        },
        {
          "name": "supporterAta",
          "writable": true
        },
        {
          "name": "usdcMint"
        },
        {
          "name": "supporter",
          "writable": true,
          "signer": true
        },
        {
          "name": "tokenProgram"
        },
        {
          "name": "systemProgram",
          "address": "11111111111111111111111111111111"
        }
      ],
      "args": [
        {
          "name": "amount",
          "type": "u64"
        }
      ]
    },
    {
      "name": "proposeResolution",
      "docs": [
        "ResolutionAgent（config.agent）のみ。DB掲載裁決の本文ハッシュとURLを根拠として記録する。"
      ],
      "discriminator": [
        19,
        68,
        181,
        23,
        194,
        146,
        152,
        252
      ],
      "accounts": [
        {
          "name": "config",
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  111,
                  110,
                  102,
                  105,
                  103
                ]
              }
            ]
          }
        },
        {
          "name": "case",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  97,
                  115,
                  101
                ]
              },
              {
                "kind": "account",
                "path": "case.id",
                "account": "case"
              }
            ]
          }
        },
        {
          "name": "agent",
          "signer": true,
          "relations": [
            "config"
          ]
        }
      ],
      "args": [
        {
          "name": "outcome",
          "type": {
            "defined": {
              "name": "outcome"
            }
          }
        },
        {
          "name": "sourceHash",
          "type": {
            "array": [
              "u8",
              32
            ]
          }
        },
        {
          "name": "sourceUri",
          "type": "string"
        }
      ]
    },
    {
      "name": "refund",
      "docs": [
        "棄却・却下・取下げで確定、または期限+猶予を過ぎても未確定 → 支援者が自分の拠出分を回収。",
        "Pledgeアカウントは close = supporter で閉じるため二重返金は起きない。"
      ],
      "discriminator": [
        2,
        96,
        183,
        251,
        63,
        208,
        46,
        46
      ],
      "accounts": [
        {
          "name": "config",
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  111,
                  110,
                  102,
                  105,
                  103
                ]
              }
            ]
          }
        },
        {
          "name": "case",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  97,
                  115,
                  101
                ]
              },
              {
                "kind": "account",
                "path": "case.id",
                "account": "case"
              }
            ]
          },
          "relations": [
            "pledge"
          ]
        },
        {
          "name": "pledge",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  112,
                  108,
                  101,
                  100,
                  103,
                  101
                ]
              },
              {
                "kind": "account",
                "path": "case"
              },
              {
                "kind": "account",
                "path": "supporter"
              }
            ]
          }
        },
        {
          "name": "caseVault",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "account",
                "path": "case"
              },
              {
                "kind": "account",
                "path": "tokenProgram"
              },
              {
                "kind": "account",
                "path": "usdcMint"
              }
            ],
            "program": {
              "kind": "const",
              "value": [
                140,
                151,
                37,
                143,
                78,
                36,
                137,
                241,
                187,
                61,
                16,
                41,
                20,
                142,
                13,
                131,
                11,
                90,
                19,
                153,
                218,
                255,
                16,
                132,
                4,
                142,
                123,
                216,
                219,
                233,
                248,
                89
              ]
            }
          }
        },
        {
          "name": "supporterAta",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "account",
                "path": "supporter"
              },
              {
                "kind": "account",
                "path": "tokenProgram"
              },
              {
                "kind": "account",
                "path": "usdcMint"
              }
            ],
            "program": {
              "kind": "const",
              "value": [
                140,
                151,
                37,
                143,
                78,
                36,
                137,
                241,
                187,
                61,
                16,
                41,
                20,
                142,
                13,
                131,
                11,
                90,
                19,
                153,
                218,
                255,
                16,
                132,
                4,
                142,
                123,
                216,
                219,
                233,
                248,
                89
              ]
            }
          }
        },
        {
          "name": "usdcMint"
        },
        {
          "name": "supporter",
          "writable": true,
          "signer": true,
          "relations": [
            "pledge"
          ]
        },
        {
          "name": "tokenProgram"
        },
        {
          "name": "associatedTokenProgram",
          "address": "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
        },
        {
          "name": "systemProgram",
          "address": "11111111111111111111111111111111"
        }
      ],
      "args": []
    },
    {
      "name": "registerProfessional",
      "docs": [
        "有資格者の登録（adminが弁護士会等の登録をオフチェーンで確認した上で行う）。",
        "登録番号そのものは載せず、ハッシュのみ記録する。"
      ],
      "discriminator": [
        81,
        251,
        120,
        209,
        94,
        212,
        93,
        9
      ],
      "accounts": [
        {
          "name": "config",
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  111,
                  110,
                  102,
                  105,
                  103
                ]
              }
            ]
          }
        },
        {
          "name": "professional",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  112,
                  114,
                  111,
                  102,
                  101,
                  115,
                  115,
                  105,
                  111,
                  110,
                  97,
                  108
                ]
              },
              {
                "kind": "arg",
                "path": "wallet"
              }
            ]
          }
        },
        {
          "name": "admin",
          "writable": true,
          "signer": true,
          "relations": [
            "config"
          ]
        },
        {
          "name": "systemProgram",
          "address": "11111111111111111111111111111111"
        }
      ],
      "args": [
        {
          "name": "wallet",
          "type": "pubkey"
        },
        {
          "name": "kind",
          "type": {
            "defined": {
              "name": "professionalKind"
            }
          }
        },
        {
          "name": "registrationHash",
          "type": {
            "array": [
              "u8",
              32
            ]
          }
        }
      ]
    },
    {
      "name": "release",
      "docs": [
        "認容・一部認容で確定 → 預託金を受益者へ解放。誰でもcrank可。"
      ],
      "discriminator": [
        253,
        249,
        15,
        206,
        28,
        127,
        193,
        241
      ],
      "accounts": [
        {
          "name": "config",
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  111,
                  110,
                  102,
                  105,
                  103
                ]
              }
            ]
          }
        },
        {
          "name": "case",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  97,
                  115,
                  101
                ]
              },
              {
                "kind": "account",
                "path": "case.id",
                "account": "case"
              }
            ]
          }
        },
        {
          "name": "beneficiary",
          "writable": true,
          "relations": [
            "case"
          ]
        },
        {
          "name": "caseVault",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "account",
                "path": "case"
              },
              {
                "kind": "account",
                "path": "tokenProgram"
              },
              {
                "kind": "account",
                "path": "usdcMint"
              }
            ],
            "program": {
              "kind": "const",
              "value": [
                140,
                151,
                37,
                143,
                78,
                36,
                137,
                241,
                187,
                61,
                16,
                41,
                20,
                142,
                13,
                131,
                11,
                90,
                19,
                153,
                218,
                255,
                16,
                132,
                4,
                142,
                123,
                216,
                219,
                233,
                248,
                89
              ]
            }
          }
        },
        {
          "name": "beneficiaryAta",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "account",
                "path": "beneficiary"
              },
              {
                "kind": "account",
                "path": "tokenProgram"
              },
              {
                "kind": "account",
                "path": "usdcMint"
              }
            ],
            "program": {
              "kind": "const",
              "value": [
                140,
                151,
                37,
                143,
                78,
                36,
                137,
                241,
                187,
                61,
                16,
                41,
                20,
                142,
                13,
                131,
                11,
                90,
                19,
                153,
                218,
                255,
                16,
                132,
                4,
                142,
                123,
                216,
                219,
                233,
                248,
                89
              ]
            }
          }
        },
        {
          "name": "usdcMint"
        },
        {
          "name": "payer",
          "writable": true,
          "signer": true
        },
        {
          "name": "tokenProgram"
        },
        {
          "name": "associatedTokenProgram",
          "address": "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
        },
        {
          "name": "systemProgram",
          "address": "11111111111111111111111111111111"
        }
      ],
      "args": []
    },
    {
      "name": "setProfessionalActive",
      "docs": [
        "登録抹消・懲戒等があった場合に無効化（以後そのウォレットを受益者とするケースは作れない）"
      ],
      "discriminator": [
        89,
        60,
        93,
        108,
        82,
        123,
        182,
        178
      ],
      "accounts": [
        {
          "name": "config",
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  111,
                  110,
                  102,
                  105,
                  103
                ]
              }
            ]
          }
        },
        {
          "name": "professional",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  112,
                  114,
                  111,
                  102,
                  101,
                  115,
                  115,
                  105,
                  111,
                  110,
                  97,
                  108
                ]
              },
              {
                "kind": "account",
                "path": "professional.wallet",
                "account": "professional"
              }
            ]
          }
        },
        {
          "name": "admin",
          "signer": true,
          "relations": [
            "config"
          ]
        }
      ],
      "args": [
        {
          "name": "active",
          "type": "bool"
        }
      ]
    },
    {
      "name": "setRoles",
      "docs": [
        "エージェント鍵・arbiterのローテーション（adminのみ）"
      ],
      "discriminator": [
        119,
        86,
        129,
        161,
        55,
        23,
        250,
        12
      ],
      "accounts": [
        {
          "name": "config",
          "writable": true,
          "pda": {
            "seeds": [
              {
                "kind": "const",
                "value": [
                  99,
                  111,
                  110,
                  102,
                  105,
                  103
                ]
              }
            ]
          }
        },
        {
          "name": "admin",
          "signer": true,
          "relations": [
            "config"
          ]
        }
      ],
      "args": [
        {
          "name": "agent",
          "type": "pubkey"
        },
        {
          "name": "arbiter",
          "type": "pubkey"
        }
      ]
    }
  ],
  "accounts": [
    {
      "name": "case",
      "discriminator": [
        24,
        222,
        147,
        26,
        60,
        162,
        154,
        176
      ]
    },
    {
      "name": "config",
      "discriminator": [
        155,
        12,
        170,
        224,
        30,
        250,
        204,
        130
      ]
    },
    {
      "name": "pledge",
      "discriminator": [
        161,
        197,
        121,
        46,
        99,
        75,
        169,
        131
      ]
    },
    {
      "name": "professional",
      "discriminator": [
        221,
        1,
        32,
        204,
        151,
        142,
        59,
        216
      ]
    }
  ],
  "events": [
    {
      "name": "caseCreated",
      "discriminator": [
        152,
        233,
        48,
        247,
        69,
        252,
        84,
        146
      ]
    },
    {
      "name": "fundsReleased",
      "discriminator": [
        178,
        119,
        252,
        230,
        131,
        104,
        210,
        210
      ]
    },
    {
      "name": "pledged",
      "discriminator": [
        98,
        154,
        68,
        227,
        24,
        48,
        26,
        16
      ]
    },
    {
      "name": "refunded",
      "discriminator": [
        35,
        103,
        149,
        246,
        196,
        123,
        221,
        99
      ]
    },
    {
      "name": "resolutionChallenged",
      "discriminator": [
        134,
        252,
        105,
        19,
        175,
        223,
        45,
        16
      ]
    },
    {
      "name": "resolutionFinalized",
      "discriminator": [
        149,
        12,
        63,
        110,
        234,
        46,
        241,
        202
      ]
    },
    {
      "name": "resolutionProposed",
      "discriminator": [
        209,
        21,
        193,
        193,
        218,
        234,
        131,
        108
      ]
    }
  ],
  "errors": [
    {
      "code": 6000,
      "name": "titleTooLong",
      "msg": "タイトルが長すぎます"
    },
    {
      "code": 6001,
      "name": "uriTooLong",
      "msg": "URIが長すぎます"
    },
    {
      "code": 6002,
      "name": "invalidDeadline",
      "msg": "期限が不正です"
    },
    {
      "code": 6003,
      "name": "zeroAmount",
      "msg": "金額は0より大きくしてください"
    },
    {
      "code": 6004,
      "name": "caseClosed",
      "msg": "このケースは支援受付を終了しています"
    },
    {
      "code": 6005,
      "name": "alreadyResolved",
      "msg": "既に結論が提案または確定しています"
    },
    {
      "code": 6006,
      "name": "resolutionExpired",
      "msg": "返金猶予期間に入ったため結論を受け付けられません"
    },
    {
      "code": 6007,
      "name": "notProposed",
      "msg": "結論が提案されていません"
    },
    {
      "code": 6008,
      "name": "challengeClosed",
      "msg": "異議申立期間は終了しています"
    },
    {
      "code": 6009,
      "name": "challengeOpen",
      "msg": "異議申立期間中です"
    },
    {
      "code": 6010,
      "name": "notChallenged",
      "msg": "異議申立されていません"
    },
    {
      "code": 6011,
      "name": "notFinal",
      "msg": "結論が確定していません"
    },
    {
      "code": 6012,
      "name": "notGranted",
      "msg": "認容・一部認容ではありません"
    },
    {
      "code": 6013,
      "name": "alreadyReleased",
      "msg": "既に解放済みです"
    },
    {
      "code": 6014,
      "name": "refundNotAvailable",
      "msg": "返金条件を満たしていません"
    },
    {
      "code": 6015,
      "name": "beneficiaryNotAllowed",
      "msg": "受益者は申立人本人または登録済みの有資格者に限られます"
    },
    {
      "code": 6016,
      "name": "overflow",
      "msg": "オーバーフロー"
    }
  ],
  "types": [
    {
      "name": "case",
      "type": {
        "kind": "struct",
        "fields": [
          {
            "name": "id",
            "type": "u64"
          },
          {
            "name": "creator",
            "type": "pubkey"
          },
          {
            "name": "beneficiary",
            "type": "pubkey"
          },
          {
            "name": "title",
            "type": "string"
          },
          {
            "name": "evidenceHash",
            "type": {
              "array": [
                "u8",
                32
              ]
            }
          },
          {
            "name": "evidenceUri",
            "type": "string"
          },
          {
            "name": "reviewKeyHash",
            "type": {
              "array": [
                "u8",
                32
              ]
            }
          },
          {
            "name": "status",
            "type": {
              "defined": {
                "name": "caseStatus"
              }
            }
          },
          {
            "name": "outcome",
            "type": {
              "option": {
                "defined": {
                  "name": "outcome"
                }
              }
            }
          },
          {
            "name": "sourceHash",
            "docs": [
              "DB掲載裁決本文のsha256"
            ],
            "type": {
              "array": [
                "u8",
                32
              ]
            }
          },
          {
            "name": "sourceUri",
            "docs": [
              "総務省DBの該当URL"
            ],
            "type": "string"
          },
          {
            "name": "proposedAt",
            "type": "i64"
          },
          {
            "name": "totalSol",
            "type": "u64"
          },
          {
            "name": "totalUsdc",
            "type": "u64"
          },
          {
            "name": "supporterCount",
            "type": "u32"
          },
          {
            "name": "deadline",
            "type": "i64"
          },
          {
            "name": "released",
            "type": "bool"
          },
          {
            "name": "bump",
            "type": "u8"
          }
        ]
      }
    },
    {
      "name": "caseCreated",
      "type": {
        "kind": "struct",
        "fields": [
          {
            "name": "case",
            "type": "pubkey"
          },
          {
            "name": "id",
            "type": "u64"
          },
          {
            "name": "creator",
            "type": "pubkey"
          },
          {
            "name": "beneficiary",
            "type": "pubkey"
          }
        ]
      }
    },
    {
      "name": "caseStatus",
      "type": {
        "kind": "enum",
        "variants": [
          {
            "name": "underReview"
          },
          {
            "name": "proposed"
          },
          {
            "name": "challenged"
          },
          {
            "name": "final"
          }
        ]
      }
    },
    {
      "name": "config",
      "type": {
        "kind": "struct",
        "fields": [
          {
            "name": "admin",
            "type": "pubkey"
          },
          {
            "name": "agent",
            "docs": [
              "ResolutionAgent（総務省DBを参照するオフチェーンエージェント）の署名鍵"
            ],
            "type": "pubkey"
          },
          {
            "name": "arbiter",
            "docs": [
              "異議時の最終判断者（Squadsマルチシグ想定）"
            ],
            "type": "pubkey"
          },
          {
            "name": "usdcMint",
            "type": "pubkey"
          },
          {
            "name": "caseCount",
            "type": "u64"
          },
          {
            "name": "bump",
            "type": "u8"
          }
        ]
      }
    },
    {
      "name": "fundsReleased",
      "type": {
        "kind": "struct",
        "fields": [
          {
            "name": "case",
            "type": "pubkey"
          },
          {
            "name": "beneficiary",
            "type": "pubkey"
          },
          {
            "name": "sol",
            "type": "u64"
          },
          {
            "name": "usdc",
            "type": "u64"
          }
        ]
      }
    },
    {
      "name": "outcome",
      "docs": [
        "行政不服審査の結論区分"
      ],
      "type": {
        "kind": "enum",
        "variants": [
          {
            "name": "granted"
          },
          {
            "name": "partiallyGranted"
          },
          {
            "name": "dismissed"
          },
          {
            "name": "rejected"
          },
          {
            "name": "withdrawn"
          }
        ]
      }
    },
    {
      "name": "pledge",
      "type": {
        "kind": "struct",
        "fields": [
          {
            "name": "case",
            "type": "pubkey"
          },
          {
            "name": "supporter",
            "type": "pubkey"
          },
          {
            "name": "solAmount",
            "type": "u64"
          },
          {
            "name": "usdcAmount",
            "type": "u64"
          },
          {
            "name": "bump",
            "type": "u8"
          }
        ]
      }
    },
    {
      "name": "pledged",
      "type": {
        "kind": "struct",
        "fields": [
          {
            "name": "case",
            "type": "pubkey"
          },
          {
            "name": "supporter",
            "type": "pubkey"
          },
          {
            "name": "solAmount",
            "type": "u64"
          },
          {
            "name": "usdcAmount",
            "type": "u64"
          }
        ]
      }
    },
    {
      "name": "professional",
      "type": {
        "kind": "struct",
        "fields": [
          {
            "name": "wallet",
            "type": "pubkey"
          },
          {
            "name": "kind",
            "type": {
              "defined": {
                "name": "professionalKind"
              }
            }
          },
          {
            "name": "registrationHash",
            "docs": [
              "sha256(登録番号) — 登録番号そのものはオンチェーンに載せない"
            ],
            "type": {
              "array": [
                "u8",
                32
              ]
            }
          },
          {
            "name": "active",
            "type": "bool"
          },
          {
            "name": "bump",
            "type": "u8"
          }
        ]
      }
    },
    {
      "name": "professionalKind",
      "docs": [
        "不服申立ての代理が法律上認められる資格の種別（業務範囲の確認はオフチェーンで行う）"
      ],
      "type": {
        "kind": "enum",
        "variants": [
          {
            "name": "lawyer"
          },
          {
            "name": "specifiedGyoseishoshi"
          },
          {
            "name": "sharoushiLaborInsurance"
          },
          {
            "name": "taxAccountant"
          }
        ]
      }
    },
    {
      "name": "refunded",
      "type": {
        "kind": "struct",
        "fields": [
          {
            "name": "case",
            "type": "pubkey"
          },
          {
            "name": "supporter",
            "type": "pubkey"
          },
          {
            "name": "sol",
            "type": "u64"
          },
          {
            "name": "usdc",
            "type": "u64"
          }
        ]
      }
    },
    {
      "name": "resolutionChallenged",
      "type": {
        "kind": "struct",
        "fields": [
          {
            "name": "case",
            "type": "pubkey"
          },
          {
            "name": "challenger",
            "type": "pubkey"
          }
        ]
      }
    },
    {
      "name": "resolutionFinalized",
      "type": {
        "kind": "struct",
        "fields": [
          {
            "name": "case",
            "type": "pubkey"
          },
          {
            "name": "outcome",
            "type": {
              "defined": {
                "name": "outcome"
              }
            }
          }
        ]
      }
    },
    {
      "name": "resolutionProposed",
      "type": {
        "kind": "struct",
        "fields": [
          {
            "name": "case",
            "type": "pubkey"
          },
          {
            "name": "outcome",
            "type": {
              "defined": {
                "name": "outcome"
              }
            }
          },
          {
            "name": "sourceHash",
            "type": {
              "array": [
                "u8",
                32
              ]
            }
          }
        ]
      }
    }
  ]
};
