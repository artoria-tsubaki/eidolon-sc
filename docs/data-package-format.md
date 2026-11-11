# 数据包的格式

数据包（`GLOSSARY.md`「数据包」）是用户在设置的「数据包」里导出的一个 zip：他自己的数据，带到这个部署或别的部署导入，接着用。它不是备份，也没有加密，是用户自己保管的明文。这份说明给别的部署者和以后的版本看：每个键的意思和版本规则。为什么好感度和礼物不在里面，见 ADR-0025。

现在的格式是**第 5 版**。第 2 版在对话里加了她的搭话（`reach_outs`），第 3 版在对话事件里加了「她不太舒服了」（`unwell`），又加了一类「周期记录」（`cycles`），第 4 版在制作人档案里加了所在城市（`city`），第 5 版加了一类「信」（`letters`），别的和第 1 版一样。

## zip 里有什么

固定这几样，别的条目一律当坏包：

| 条目 | 说明 |
| --- | --- |
| `manifest.json` | zip 里的**第一个**条目，不压缩（stored），页面只读文件开头就能拿到它。 |
| `photos/<照片 id>.webp` | 每张发出来了的照片一个文件，就是这个部署存的 WebP 原样，解压就能打开。没带照片的包里没有。 |
| `data.json` | 缩进排版、UTF-8、英文键名的数据。 |

## `manifest.json`

```json
{
  "format": 6,
  "exported_at": "2026-10-19T08:00:00.000000+00:00",
  "categories": ["conversations", "photos", "memories", "profile", "cycles", "letters"],
  "counts": { "conversations": 12, "photos": 3, "memories": 40, "profile": 1, "cycles": 9, "letters": 2 }
}
```

- `format`：整数，这个包的格式版本。
- `exported_at`：导出的时间。
- `categories`：这个包里有哪几类，按 `conversations`、`photos`、`memories`、`profile`、`cycles`、`letters` 的顺序。导出时没勾的类别不在这里。
- `counts`：每类的条数，键同 `categories`。`profile` 是 1，`conversations` 是对话的段数，`photos` 是发出来了的照片的张数（就是 `photos/` 里的文件数，没发出去的不算），`memories` 是记忆的条数，`cycles` 是周期记录的条数，`letters` 是信的封数。

## `data.json`

`categories` 里的每一类在这里有一个键，没有的类别就没有这个键。照片没有自己的键，挂在对话里她的回复上（见下面的 `photo`）。

### `profile`：制作人档案

```json
{
  "profile": {
    "form_of_address": "小林",
    "gender": "female",
    "timezone": "Asia/Tokyo",
    "birthday_month": 2,
    "birthday_day": 29,
    "city": {
      "name": "东城",
      "adm2": "北京",
      "adm1": "北京市",
      "country": "中国",
      "latitude": 39.91755,
      "longitude": 116.41876
    }
  }
}
```

- `form_of_address`：她怎么称呼制作人，最多 20 个字；`null` 是按她自己的习惯。
- `gender`：`"male"`、`"female"` 或 `null`（和原作一致，男性）。
- `timezone`：IANA 时区名；`null` 是跟着设备。
- `birthday_month`、`birthday_day`：生日的月和日，没有年；要么都有、要么都是 `null`。2 月 29 日可以。
- `city`：制作人所在的城市（或区县），和风天气给的中文名：`name` 是它自己，`adm2` 是所在的市，`adm1` 是省或州，`country` 是国家，各最多 40 个字，除 `name` 外可以是空字符串；`latitude`、`longitude` 是它的中心坐标（WGS-84），不是用户自己的位置。`null` 是没设。第 4 版起才有，更老的包里出现就当坏包；老的包没有这个键，导入时当作 `null`。

导入时勾了「制作人档案」就整份覆盖：包里是 `null` 的项，这里也清空、沿用默认。和这里现有的一模一样时不算导入了档案。

### `conversations`：对话

和每位角色的每段对话（`GLOSSARY.md`「对话」），按开始的先后排，里面的东西照库里原样：

```json
{
  "conversations": [
    {
      "id": "3f2a…",
      "character_id": "mano",
      "character_name": "櫻木真乃",
      "created_at": "2026-09-25T12:00:00.000000+00:00",
      "title": "商量周末去看海",
      "messages": [
        {
          "role": "user", "text": "周末去看海吧", "ja": null, "zh": null, "segments": [],
          "created_at": "2026-09-25T12:00:01.000000+00:00", "affinity": null,
          "hurt": 0.0, "out_of_line": false
        },
        {
          "role": "character", "text": null, "ja": "[嬉しそうに]いいですね", "zh": "好啊",
          "segments": [{ "ja": "[嬉しそうに]いいですね", "zh": "好啊" }],
          "created_at": "2026-09-25T12:00:03.000000+00:00",
          "affinity": { "up": true, "tier_changed": false, "tier": "trust", "cooldown": false, "thawed": false },
          "hurt": null, "out_of_line": null
        }
      ],
      "events": [
        {
          "kind": "gift", "created_at": "2026-09-25T12:05:00.000000+00:00",
          "affinity": null, "gift_id": "pudding", "liked": true,
          "first_day": null, "days_ago": null
        },
        {
          "kind": "unwell", "created_at": "2026-09-27T10:59:59.999999+00:00",
          "affinity": null, "gift_id": null, "liked": false,
          "first_day": "2026-09-25", "days_ago": 2
        }
      ],
      "calls": [{ "connected_at": "2026-09-25T13:00:00.000000+00:00", "duration_s": 75.5 }],
      "reach_outs": [
        {
          "ja": "[嬉しそうに]プロデューサーさん！レッスン終わりました", "zh": "制作人！练习结束了",
          "segments": [
            { "ja": "[嬉しそうに]プロデューサーさん！", "zh": "制作人！" },
            { "ja": "レッスン終わりました", "zh": "练习结束了" }
          ],
          "created_at": "2026-09-26T09:00:00.000000+00:00"
        }
      ]
    }
  ]
}
```

- `id`：包内 ID，整数或字符串，只在这个包里当键用（导出时是它原来的 id），记忆的 `conversation` 引用它；包里不能重复。
- `character_id`、`character_name`：和谁的对话。名字给人看，也用在「跳过了和谁的几段对话」里；导出的部署上已经没有她时是 `null`。
- `created_at`：对话开始的时间。`title`：对话标题，还没起时是 `null`。
- `messages`：按原来的先后排（导入时照这个顺序插入）。`role` 是 `"user"`（制作人说的，在 `text` 里）或 `"character"`（她的回复：`ja` 是日文原文，`zh` 是中文字幕，`segments` 是一句句对上的日文和字幕）。她的回复照模型写的原样，带着语气提示（ADR-0007）。`affinity` 是这个回合让好感度怎么变了，`null` 是没变；`hurt`、`out_of_line` 是打字的回合让她多不高兴、是不是说了不该说的话，通话里的回合和更早的记录是 `null`。
- `events`：对话事件（`GLOSSARY.md`「对话事件」）。`kind` 是 `"hung_up"`（她挂断了电话）、`"gift"`（送了礼物，`gift_id` 是哪件、`liked` 是送的时候她喜不喜欢）、`"ended"`（她结束了这段对话，之后只能看）或 `"unwell"`（她不太舒服了：她经期里制作人第一次来时，记在那个回合之前，只给用户看）。`gift_id` 只有送礼事件有，送礼事件也必须有。`first_day`、`days_ago` 只有 `"unwell"` 有，它也必须有：`first_day` 是这次经期的第一天（日历日，`YYYY-MM-DD`，按制作人的时区），`days_ago` 是它比记下那天早几天（从 0 起）；其他事件是 `null` 或者没有这两个键。`"unwell"` 第 3 版起才有，更早的包里有就当坏包。她真实的周期不在包里。`affinity` 同上。
- `calls`：这段对话里接通了的通话，只给统计用：`connected_at` 是接通的时间，`duration_s` 是接通后持续了几秒（不小于 0）。
- `reach_outs`：她在这段对话里的搭话（`GLOSSARY.md`「搭话」），按原来的先后排：`ja`、`zh`、`segments` 同她的回复，照模型写的原样带着语气提示；`created_at` 是她说的时间。第 2 版起才有：第 1 版的包没有这个键，有就当坏包；第 2 版起的包里没有这个键，当没有搭话。用户看没看过、她下次什么时候搭话、用户的「搭话」开关，都不在包里。

#### `photo`：她发的照片

`categories` 里有 `photos` 时（只能和 `conversations` 一起），她发过照片的那条回复上多一个 `photo`（`GLOSSARY.md`「照片」）；没有照片的消息、没带照片的包，就没有这个键。

```json
{
  "role": "character", "ja": "はい、どうぞ", "zh": "给，你看", "…": "…",
  "photo": {
    "id": "2451db2291f6484bb1887811cd197b74",
    "tag": "shot: selfie\nframing: upper body\n…",
    "status": "ready", "reason": null,
    "created_at": "2026-09-25T12:00:03.000000+00:00",
    "updated_at": "2026-09-25T12:00:20.000000+00:00"
  }
}
```

- `id`：包内 ID，字母、数字、`_`、`-`，最多 100 个字符；包里不能重复。发出来了的照片，图片就是 `photos/<id>.webp`。
- `tag`：她给这张照片写的照片标签，照原样，只进她之后的提示词。
- `status`：`"ready"`（发出来了，`photos/` 里有它的图）或 `"failed"`（没发出去）。导出时还在生成的，按 `"failed"`、原因 `"timeout"` 导出，和服务器重启时一样。
- `reason`：没发出去的原因，`"ready"` 时是 `null`，`"failed"` 时必须有：`"timeout"`（生成太久）、`"service_error"`（生图服务出错或连不上）、`"bad_image"`（回来的不是一张对的图）、`"own_key_failed"`（自带的生图密钥不能用）、`"used_up"`（这一小时的照片用完了）。
- `created_at`、`updated_at`：她发出这张照片的时间，和它最后一次变（发出来或没发出去）的时间。

导入时，照片换新 id、挂到新加的那条回复上；整段跳过的对话（角色不在、已经有了），里面的照片也跳过。每个 `photos/` 里的文件都要能当 WebP 解开（尺寸不查，别的版本的照片尺寸可能不同），`"ready"` 的照片必须有文件，文件必须对得上一张 `"ready"` 的照片，否则整包当坏包。导入时没勾照片的，她的回复上就不带照片。

导入时：

- 角色不在这个部署的角色配置里的，整段跳过，连同通话记录，结果里的 `skipped` 带上包里的角色名（没有名字时是 `character_id`）和段数。
- 这位用户和同一位角色、开始时间相同的对话已经有了，就当成同一段，整段跳过，不逐条合并；包里的记忆引用它时，改成引用已有的那段。
- 其余的换新 id 加进去，带来的搭话算看过（`GLOSSARY.md`「没看过的搭话」）。当前对话照旧是最新开始的那段：在这个部署上新聊的仍是当前对话，导入的进历史对话；被她结束的仍是已结束。
- 送礼事件里的礼物不在这个部署的礼物表里的，这一条跳过。
- 好感度、抽奖次数、重置卡、礼物背包、成就的进度、日程、酒吧的光顾记录都不在数据包里，导入不碰（ADR-0025）。

### `memories`：记忆

制作人告诉过她们的事（`GLOSSARY.md`「记忆」），被取代的也在，按记下的先后排：

```json
{
  "memories": [
    {
      "id": 41,
      "source_character_id": "mano",
      "source_character_name": "櫻木真乃",
      "conversation": null,
      "text": "制作人住在大阪",
      "created_at": "2026-09-25T12:00:00.000000+00:00",
      "superseded_by": 42
    },
    {
      "id": 42,
      "source_character_id": "kogane",
      "source_character_name": "月岡恋鐘",
      "conversation": null,
      "text": "制作人从大阪搬到了东京",
      "created_at": "2026-09-26T12:00:00.000000+00:00",
      "superseded_by": null
    }
  ]
}
```

- `id`：包内 ID，整数或字符串，只在这个包里当键用（导出时是它原来的 id）；包里不能重复。
- `source_character_id`、`source_character_name`：来源角色，记忆是告诉谁的。名字只给人看，导入时不用；导出的部署上已经没有她时是 `null`。
- `conversation`：在包里哪段对话里说的（包内对话 ID），必须引用得上 `conversations` 里的一段；`null` 是不知道或不在包里（导出时没带对话就一律是 `null`）。导入时对应到这里的那段，那段被跳过或没导入对话时存成空。
- `text`：记忆的原文，1 到 200 个字。
- `created_at`：记下的时间。
- `superseded_by`：取代它的那条记忆的包内 ID，必须是包里的另一条；`null` 是它还作数（ADR-0014）。

导入时按原文去重：这位用户已经有同样原文的记忆，就不新加，包里指向它的取代关系对应到已有的那条；已有的记忆一律不改，包括它们的取代关系。来源角色照原样存，这个部署上没有她也照样导入。导入后服务器在后台给新加的记忆算向量。

### `cycles`：周期记录

第 3 版起才有。用户在周期页上替每位角色记下的经期（`GLOSSARY.md`「周期」），按第一天的先后排：

```json
{
  "cycles": [
    { "character_id": "mano", "first_day": "2026-08-27", "days": 4 },
    { "character_id": "kogane", "first_day": "2026-09-03", "days": 5 },
    { "character_id": "mano", "first_day": "2026-09-25", "days": 6 }
  ]
}
```

- `character_id`：记的是哪位角色。
- `first_day`：这次经期的第一天，日历日 `YYYY-MM-DD`，按记下时制作人所在的时区。
- `days`：几天，整数。

每条只有这三个键，多了当坏包；记录的 id、记下的时间、推算和她真实的周期都不在包里。更早的包里有这一类就当坏包。

导入时并进已有的记录，一条一条按包里的顺序看，下面几种跳过，结果里的 `cycles_skipped` 是跳过的条数，`cycles` 是导入的条数：

- 角色不在这个部署的角色配置里。
- `days` 不在 1 到 10 之间。
- `first_day` 晚于导入时制作人的今天（按导入后档案的时区，没有就按设备的）。
- 和这位角色已有的记录重叠，包括这次导入里排在前面、已经并进去的；重叠时留已有的。前一次最后一天的第二天开始的，不算重叠。

周期在这个部署上关着时（部署者关了或用户自己关了）也照样导出、导入。

### `letters`：信

第 5 版起才有。她写给制作人的信（`GLOSSARY.md`「信」），按写好的先后排：

```json
{
  "letters": [
    {
      "character_id": "mano",
      "character_name": "櫻木真乃",
      "occasions": ["close"],
      "day": "2026-09-25",
      "written_at": "2026-09-25T04:00:00.000000+00:00",
      "salutation": "[softly] プロデューサーさんへ",
      "paragraphs": [
        { "ja": "[smiling] いつもありがとうございます。", "zh": "一直以来谢谢你。" },
        { "ja": "これからもよろしくお願いします。", "zh": "今后也请多关照。" }
      ]
    }
  ]
}
```

- `character_id`、`character_name`：写信的角色。名字只给人看，导入时不用；导出的部署上已经没有她时是 `null`。
- `occasions`：为什么写，至少一个、不重复：`close`（第一次升到亲近）、`intimate`（第一次升到亲密）、`complete`（集齐）、`her_birthday`（她的生日）、`producer_birthday`（制作人的生日）、`anniversary`（相识纪念）。别的值当坏包。
- `day`：写信那天，日历日 `YYYY-MM-DD`，按制作人所在的时区。
- `written_at`：写好的时间。
- `salutation`：抬头，日语，照模型写的原样带着语气提示。
- `paragraphs`：各段，至少一段：`ja` 是日语原文，带着语气提示；`zh` 是中文。

她的语音、读没读过、弹没弹过都不在包里。导入的信算读过、弹过，没有语音，只有字和配乐。角色不在这个部署的角色配置里的跳过，结果里的 `letters_skipped` 是跳过的封数；这位用户已经有她同一时间写好的信，就当是这一封、不再加。结果里的 `letters` 是导入的封数。信在这个部署上关着时也照样导出、导入。更早的包里有这一类就当坏包。

## 时间

所有时间都是带时区的 ISO 8601。导出成库里的写法：UTC、带微秒，比如 `2026-10-19T08:00:00.000000+00:00`。导入时先解析，再按同一种写法写回；不带时区或解析不了的，整包当坏包。

## 版本规则

- 导入时先看 `manifest.json` 的 `format`，再看别的：比这个部署支持的新，就拒绝（错误码 `too_new`），请部署者升级，不管包里别的条目长什么样；等于或更老的，按那一版的规则读。
- 改格式时 `format` 加一，旧版本的读法保留：新版本能导入所有老格式。
- 坏包一律拒绝（错误码 `broken`）：zip 打不开，条目多了或少了，JSON 不合这份说明，时间解析不了，包内 ID 引用不上，`categories` 和 `data.json` 里的键对不上，照片文件解不开或和照片对不上。
- 导入要么全部写进去，要么什么也不写。
- 同一个包导入两次，第二次什么也不多出来：对话按开始时间、记忆按原文认出已经有了的，周期记录和已有的重叠就跳过，信按角色和写好的时间认出已经有了的，档案和这里一样就不算导入。

## 大小

- 整个数据包最大 200 MB，超了不导入（错误码 `too_large`），可以不带照片重新导出。
- 解压后 `manifest.json` 最大 64 KB，`data.json` 最大 200 MB，每张照片的文件最大 20 MB，超了当坏包。

## 格式 6：用户图片

消息增加可选 `user_image`（经服务器规范化的 WebP，base64 编码）和 `image_observation`（JSON 字符串，visible/text/relevant/uncertain）。仅 role=user 的消息可以携带用户图片，只有选择 photos 类别时导出这些字段。图片最多 10 MB，导入会重新校验和规范化，不信任外部尺寸或路径，并重新分配内部图片 ID。旧格式没有这些字段，继续正常导入；旧版本拒绝格式 6，避免静默丢失用户图片。
