import Init

namespace Warranted

def UniquenessTarget : Prop :=
  ∀ (ids : List String) (f : String → String),
    (∀ a b, f a = f b → a = b) →
    ids.Nodup → (ids.map f).Nodup

theorem uniqueness_preserved : UniquenessTarget := by sorry

end Warranted
