# 两轮送礼物 Agent Chat 测试脚本

用途：验证复杂礼物选择对话中的记忆形成、候选否决、满意确认、decision/history 迁移，以及第二次送礼物时的重复过滤。

## 目标

- 第一轮：从空白状态开始，协商出一个用户满意的礼物，并确认送出。
- 第二轮：再次选礼物时，系统应记住上次已选/已送方案，避开重复推荐。
- 对比重点：memory agent 是否比 no-memory baseline 用更少轮数、更少用户解释成本达到满意。

## Session 1：第一次送礼物

### Turn 1

user:

```text
reset memory
```

expected memory:

```text
Memory reset to M0
```

### Turn 2

user:

```text
帮我给女朋友选个礼物。
```

expected agent:

```text
给出一个具体默认推荐，而不是只列方向。
```

expected memory:

```text
candidate.proposed: 小众香氛礼盒
```

### Turn 3

user:

```text
1000 元左右的。
```

expected memory:

```text
constraint: 给女朋友选礼物预算在 1000 元以内
```

### Turn 4

user:

```text
可是她知道我前女友之前也收过这个香水，跟我大吵了一架。
```

expected agent:

```text
避开香水/香氛方向，换到其他品类。
```

expected memory:

```text
constraint: 给女朋友选礼物需避开前女友相关香水/香氛
```

### Turn 5

user:

```text
首饰的话她喜欢玫瑰金色的。
```

expected agent:

```text
可以推荐玫瑰金首饰。
```

expected memory:

```text
preference: 女朋友喜欢玫瑰金
candidate.proposed: 玫瑰金耳钉
```

### Turn 6

user:

```text
玫瑰金耳钉太多了，换一个。
```

expected agent:

```text
不再推荐玫瑰金耳钉，切到新候选，例如银色手链。
```

expected memory:

```text
constraint/current_task: 当前给女朋友选礼物时不再推荐玫瑰金耳钉
candidate.proposed: 银色手链
```

### Turn 7

user:

```text
后来又问了下她，说手链的话颜色更喜欢银色。
```

expected agent:

```text
推荐银色手链。
```

expected memory:

```text
preference: 手链的话女朋友更喜欢银色
```

### Turn 8

user:

```text
这个可以，我挺满意的，就这个，确认送出。
```

expected agent:

```text
确认最终方案是银色手链，并说明后续会避开它。
```

expected memory:

```text
decision.accepted: 本次给女朋友的礼物已选定为银色手链
history.gave: 已经给女朋友送过银色手链
satisfaction evidence: 用户满意并确认方案
```

## Session 2：第二次送礼物

### Turn 9

user:

```text
第二次送礼物，已经选过的不要再选，给我一个推荐。
```

expected agent:

```text
避开银色手链，也不要回退到已经被否过的玫瑰金耳钉；给出一个新的具体推荐。
```

expected memory:

```text
constraint: 给女朋友选礼物时，已经选过或送过的礼物不要重复：银色手链
```

## 关键断言

- “这个”必须能指向最近候选 `银色手链`。
- “满意/就这个”必须形成 `decision.accepted`。
- “确认送出”必须形成 `history.gave`。
- 第二轮“已经选过的不要再选”必须绑定到 `银色手链`，不能只写成无对象泛约束。
- 第二轮不能推荐 `银色手链`。
- 第二轮不能推荐已被用户否掉的 `玫瑰金耳钉`。
- 第二轮不应推荐已被前女友事件污染的香水/香氛方向。

## A/B 对比建议

同一脚本同时跑两条线：

- A：Memory Agent，允许使用 skill memory。
- B：Baseline Agent，不使用 skill memory，只根据当前输入回答。

对比指标：

- 到用户满意确认的轮数。
- 用户补充说明的次数。
- 用户纠错次数。
- 用户重复说明已给信息的次数。
- 用户总输入字数。
- 是否重复推荐已否/已送方案。
