from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.db.session import get_db
from app.api.deps import get_current_active_user
from app.models.user import User
from app.models.address import Address
from app.schemas.user import UserResponse, UserUpdate
from app.schemas.address import AddressCreate, AddressResponse, AddressUpdate
from app.utils.response import success

router = APIRouter()


@router.get("/me", response_model=dict)
def get_current_user_profile(current_user: User = Depends(get_current_active_user)):
    """Get current user profile"""
    return success(data=current_user, message="User profile retrieved")


@router.put("/me", response_model=dict)
def update_user_profile(
    user_update: UserUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Update user profile"""
    if user_update.full_name:
        current_user.full_name = user_update.full_name
    
    if user_update.phone:
        # Check if phone already exists
        existing_phone = db.query(User).filter(
            User.phone == user_update.phone,
            User.id != current_user.id
        ).first()
        
        if existing_phone:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Phone number already in use"
            )
        
        current_user.phone = user_update.phone
    
    db.commit()
    db.refresh(current_user)
    
    return success(data=current_user, message="User profile updated")


# ============= ADDRESSES =============

@router.get("/me/addresses", response_model=dict)
def get_user_addresses(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get user's addresses"""
    addresses = db.query(Address).filter(Address.user_id == current_user.id).all()
    return success(data=addresses, message="Addresses retrieved")


@router.post("/me/addresses", response_model=dict, status_code=status.HTTP_201_CREATED)
def create_address(
    address_data: AddressCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Add new address"""
    # If this is default, unset other defaults
    if address_data.is_default:
        db.query(Address).filter(
            Address.user_id == current_user.id,
            Address.is_default == True
        ).update({"is_default": False})
    
    address = Address(
        user_id=current_user.id,
        **address_data.model_dump()
    )
    
    db.add(address)
    db.commit()
    db.refresh(address)
    
    return success(data=address, message="Address created")


@router.put("/me/addresses/{address_id}", response_model=dict)
def update_address(
    address_id: int,
    address_update: AddressUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Update address"""
    address = db.query(Address).filter(
        Address.id == address_id,
        Address.user_id == current_user.id
    ).first()
    
    if not address:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Address not found"
        )
    
    # If setting as default, unset others
    if address_update.is_default:
        db.query(Address).filter(
            Address.user_id == current_user.id,
            Address.id != address_id,
            Address.is_default == True
        ).update({"is_default": False})
    
    # Update fields
    update_data = address_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(address, field, value)
    
    db.commit()
    db.refresh(address)
    
    return success(data=address, message="Address updated")


@router.delete("/me/addresses/{address_id}")
def delete_address(
    address_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Delete address"""
    address = db.query(Address).filter(
        Address.id == address_id,
        Address.user_id == current_user.id
    ).first()
    
    if not address:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Address not found"
        )
    
    db.delete(address)
    db.commit()
    
    return success(message="Address deleted")
