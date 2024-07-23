/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This software may be used and distributed according to the terms of the
 * GNU General Public License version 2.
 */

#include "eden/fs/store/ScmStatusCache.h"
#include <folly/portability/GTest.h>
#include "eden/fs/config/EdenConfig.h"
#include "eden/fs/journal/JournalDelta.h"
#include "eden/fs/service/gen-cpp2/eden_types.h"
#include "eden/fs/store/ObjectCache.h"
#include "eden/fs/telemetry/EdenStats.h"

using namespace facebook::eden;

struct ScmStatusCacheTest : ::testing::Test {
  std::shared_ptr<EdenConfig> rawEdenConfig;

  void SetUp() override {
    rawEdenConfig = EdenConfig::createTestEdenConfig();
  }

  ScmStatus extractStatus(
      std::variant<StatusResultFuture, StatusResultPromise> result) {
    return std::move(*std::get_if<StatusResultFuture>(&result)).get();
  }
};

TEST_F(ScmStatusCacheTest, insert_sequence_status_pair) {
  auto key = ObjectId::fromHex("0123456789abcdef");
  auto cache =
      ScmStatusCache::create(rawEdenConfig.get(), makeRefPtr<EdenStats>());
  EXPECT_FALSE(cache->contains(key));
  EXPECT_EQ(0, cache->getObjectCount());

  JournalDelta::SequenceNumber sequenceId = 5;
  JournalDelta::SequenceNumber seqSmall = 4;
  JournalDelta::SequenceNumber seqLarge = 6;

  ScmStatus initialStatus;
  initialStatus.entries_ref()->emplace("foo", ScmFileStatus::ADDED);
  ScmStatus secondStatus;
  ScmStatus thirdStatus;
  initialStatus.entries_ref()->emplace("bar", ScmFileStatus::ADDED);

  auto val = std::make_shared<SeqStatusPair>(sequenceId, initialStatus);
  cache->insert(key, val);
  EXPECT_TRUE(cache->contains(key));
  EXPECT_EQ(1, cache->getObjectCount());
  auto statusRes = extractStatus(cache->get(key, sequenceId));
  EXPECT_EQ(initialStatus, statusRes);

  // because the sequence number is smaller the
  // orignal value should stay in the cache
  val = std::make_shared<SeqStatusPair>(seqSmall, secondStatus);
  cache->insert(key, val);
  EXPECT_TRUE(cache->contains(key));
  EXPECT_EQ(1, cache->getObjectCount());
  statusRes = extractStatus(cache->get(key, sequenceId));
  EXPECT_EQ(initialStatus, statusRes);

  // because the sequence number is larger the
  // value in the cache should be replaced.
  val = std::make_shared<SeqStatusPair>(seqLarge, thirdStatus);
  cache->insert(key, val);
  EXPECT_TRUE(cache->contains(key));
  EXPECT_EQ(1, cache->getObjectCount());
  statusRes = extractStatus(cache->get(key, sequenceId));
  EXPECT_EQ(thirdStatus, statusRes);
}

TEST_F(ScmStatusCacheTest, evict_when_cache_size_too_large) {
  ScmStatus status;
  auto sizeOfStatus =
      sizeof(status); // this is different for different platforms: Linux: 104,
                      // Mac: 56, Windows: 40
  status.entries_ref().value().emplace(
      "f1234", ScmFileStatus::ADDED); // entry size = 6 + 4 = 10 bytes
  // Total size of a cache item = sizeof(sequence) + sizeof(ScmStatus) + 10
  auto totalItemSize = 8 + sizeOfStatus + 10;

  // A cache with maximum size=600 bytes
  rawEdenConfig->scmStatusCacheMaxSize.setValue(
      600, ConfigSourceType::CommandLine);

  auto cache =
      ScmStatusCache::create(rawEdenConfig.get(), makeRefPtr<EdenStats>());

  int maxItemCnt = 600 / totalItemSize;

  std::vector<ObjectId> keys;

  for (auto i = 1; i <= maxItemCnt + 1; i++) {
    keys.push_back(

        ObjectId::sha1(fmt::format("{}", i)));

    cache->insert(keys.back(), std::make_shared<SeqStatusPair>(i, status));

    if (i <= maxItemCnt) {
      EXPECT_EQ(i, cache->getObjectCount());
    } else {
      EXPECT_EQ(maxItemCnt, cache->getObjectCount());
    }
  }

  EXPECT_FALSE(cache->contains(keys.front()));
}

TEST_F(ScmStatusCacheTest, evict_on_update) {
  ScmStatus status;
  auto sizeOfStatus =
      sizeof(status); // this is different for different platforms: Linux: 104,
                      // Mac: 56, Windows: 40
  status.entries_ref().value().emplace(
      "f1234", ScmFileStatus::ADDED); // entry size = 6 + 4 = 10 bytes
  // Total size of a cache item = sizeof(sequence) + sizeof(ScmStatus) + 10
  auto totalItemSize = 8 + sizeOfStatus + 10;

  // A cache with maximum size=600 bytes
  rawEdenConfig->scmStatusCacheMaxSize.setValue(
      600, ConfigSourceType::CommandLine);

  int maxItemCnt = 600 / totalItemSize;

  rawEdenConfig->scmStatusCacheMininumItems.setValue(
      maxItemCnt - 1, ConfigSourceType::CommandLine);

  auto cache =
      ScmStatusCache::create(rawEdenConfig.get(), makeRefPtr<EdenStats>());

  std::vector<ObjectId> keys;
  for (auto i = 0; i < maxItemCnt; i++) {
    keys.push_back(ObjectId::sha1(fmt::format("{}", i)));
    cache->insert(keys.back(), std::make_shared<SeqStatusPair>(i, status));
  }

  EXPECT_EQ(maxItemCnt, cache->getObjectCount());

  ScmStatus statusWithManyEntries;
  for (auto i = 0; i < 100; i++) {
    statusWithManyEntries.entries_ref().value().emplace(
        fmt::format("file{}", i), ScmFileStatus::ADDED);
  }

  auto v = std::make_shared<SeqStatusPair>(1, statusWithManyEntries);

  // this should evict the the cache size to be maxItemCnt-1
  cache->insert(keys.front(), v);
  EXPECT_EQ(maxItemCnt - 1, cache->getObjectCount());
}

TEST_F(ScmStatusCacheTest, drop_cached_promise) {
  auto cache =
      ScmStatusCache::create(rawEdenConfig.get(), makeRefPtr<EdenStats>());

  ScmStatus status;
  status.entries_ref()->emplace("foo", ScmFileStatus::ADDED);

  auto key = ObjectId::sha1("foo");
  auto getResult_0 = cache->get(key, 1);

  EXPECT_FALSE(std::holds_alternative<StatusResultFuture>(getResult_0));

  auto getResult_1 = cache->get(key, 1);
  EXPECT_TRUE(std::holds_alternative<StatusResultFuture>(getResult_1));
  auto future_1 = std::move(std::get<StatusResultFuture>(getResult_1));
  EXPECT_FALSE(future_1.isReady());

  cache->dropPromise(key, 1);
  auto promise = std::get<StatusResultPromise>(getResult_0);
  promise->setValue(status);

  // check promise is still valid after being dropped
  ASSERT_NE(future_1.isReady(), detail::kImmediateFutureAlwaysDefer);
  EXPECT_EQ(status, std::move(future_1).get());

  auto getResult_2 = cache->get(key, 1);
  EXPECT_FALSE(std::holds_alternative<StatusResultFuture>(getResult_2));

  // droping a promise with sequence smaller should be noop
  cache->dropPromise(key, 0);
  auto getResult_3 = cache->get(key, 1);
  EXPECT_TRUE(std::holds_alternative<StatusResultFuture>(getResult_3));
}

TEST_F(ScmStatusCacheTest, get_results_as_promise_or_future) {
  auto cache =
      ScmStatusCache::create(rawEdenConfig.get(), makeRefPtr<EdenStats>());

  ScmStatus status;
  status.entries_ref()->emplace("foo", ScmFileStatus::ADDED);

  auto key = ObjectId::sha1("foo");
  EXPECT_FALSE(cache->contains(key));

  auto getResult_0 = cache->get(key, 1);
  EXPECT_FALSE(cache->contains(key));
  EXPECT_TRUE(std::holds_alternative<StatusResultPromise>(getResult_0));
  auto promise = std::get<StatusResultPromise>(getResult_0);

  std::vector<StatusResultFuture> futures;
  for (int i = 0; i < 10; i++) {
    auto getResult = cache->get(key, 1);
    EXPECT_FALSE(cache->contains(key));
    EXPECT_TRUE(std::holds_alternative<StatusResultFuture>(getResult));
    futures.push_back(std::move(std::get<StatusResultFuture>(getResult)));
    EXPECT_FALSE(futures.back().isReady());
  }

  promise->setValue(status);

  for (auto& future : futures) {
    ASSERT_NE(future.isReady(), detail::kImmediateFutureAlwaysDefer);
    EXPECT_FALSE(future.debugIsImmediate());
    EXPECT_EQ(status, std::move(future).get());
  }

  for (int i = 0; i < 10; i++) {
    auto getResult = cache->get(key, 1);
    EXPECT_FALSE(cache->contains(key));
    EXPECT_TRUE(std::holds_alternative<StatusResultFuture>(getResult));
    auto future = std::move(std::get<StatusResultFuture>(getResult));
    ASSERT_NE(future.isReady(), detail::kImmediateFutureAlwaysDefer);
    ASSERT_NE(future.debugIsImmediate(), detail::kImmediateFutureAlwaysDefer);
    EXPECT_EQ(status, (std::move(future)).get());
  }

  cache->insert(key, std::make_shared<SeqStatusPair>(1, status));
  EXPECT_TRUE(cache->contains(key));

  for (int i = 0; i < 10; i++) {
    auto getResult = cache->get(key, 1);
    EXPECT_TRUE(std::holds_alternative<StatusResultFuture>(getResult));
    auto future = std::move(std::get<StatusResultFuture>(getResult));
    ASSERT_NE(future.isReady(), detail::kImmediateFutureAlwaysDefer);
    ASSERT_NE(future.debugIsImmediate(), detail::kImmediateFutureAlwaysDefer);
    EXPECT_EQ(status, (std::move(future)).get());
  }
}
