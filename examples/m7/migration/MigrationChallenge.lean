import Init

namespace Warranted

structure OldConfiguration where
  host : String
  timeout : Nat
  label : Option String

structure NewConfiguration where
  endpoint : String
  timeoutSeconds : Nat
  label : Option String

inductive Configuration where
  | old : OldConfiguration → Configuration
  | current : NewConfiguration → Configuration

def migrate (keepLabel : Bool) : Configuration → NewConfiguration
  | .old c => ⟨c.host, c.timeout, if keepLabel then c.label else none⟩
  | .current c => c

def endpoint : Configuration → String
  | .old c => c.host
  | .current c => c.endpoint

def timeoutSeconds : Configuration → Nat
  | .old c => c.timeout
  | .current c => c.timeoutSeconds

def MigrationTarget : Prop :=
  ∀ (keepLabel : Bool) (c : Configuration),
    (migrate keepLabel c).endpoint = endpoint c ∧
    (migrate keepLabel c).timeoutSeconds = timeoutSeconds c

theorem migration_renaming : MigrationTarget := by sorry

end Warranted
