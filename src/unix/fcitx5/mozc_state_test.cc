// Copyright 2026 The Mozkey Authors
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "unix/fcitx5/mozc_state.h"

#include <fcitx/inputcontext.h>
#include <fcitx/inputcontextmanager.h>
#include <fcitx/inputcontextproperty.h>

#include <string>

#include "testing/gunit.h"

namespace fcitx {
namespace {

class ConstructionInputContext final : public InputContext {
 public:
  explicit ConstructionInputContext(InputContextManager& manager)
      : InputContext(manager, "construction-test") {
    created();
  }

  ~ConstructionInputContext() override { destroy(); }

  const char* frontend() const override { return "construction-test"; }

 protected:
  void commitStringImpl(const std::string&) override {}
  void deleteSurroundingTextImpl(int, unsigned int) override {}
  void forwardKeyImpl(const ForwardKeyEvent&) override {}
  void updatePreeditImpl() override {}
};

TEST(MozcStateTest, ConstructorDefersInputContextAndEngineAccessUntilFocusIn) {
  InputContextManager manager;
  FactoryFor<MozcState> factory([](InputContext& ic) {
    // InputContextManager creates registered properties from InputContext's
    // base constructor.  Its derived frontend is not valid at that point.
    // A null engine makes this test also enforce that MozcState construction
    // performs no client or configuration access before FocusIn.
    return new MozcState(&ic, /*engine=*/nullptr);
  });
  ASSERT_TRUE(manager.registerProperty("mozc-state-construction", &factory));

  ConstructionInputContext input_context(manager);
  EXPECT_NE(input_context.propertyFor(&factory), nullptr);
}

}  // namespace
}  // namespace fcitx
